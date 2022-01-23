#!/usr/bin/python3
"""An agent for an edge computer of the LIQUID_EDGE PRIN 2017 research project

This client script should run as a daemon right from the beginning, and provides
computational offloading to light mobile clients.
"""

# import section
from __future__ import annotations

import argparse
import datetime
import logging
import os
from pathlib import Path
import platform
import psutil
import random
import socket
import string
import sys
import subprocess
import tempfile
import threading
import time
import uuid
import yaml
import paramiko

import cpuinfo
import GPUtil

import liquidedge

from liquidedge import edgecomputer, mobilenode, cloudorchestrator, pu, engine, isNaN
from littlefsm import State, FiniteStateMachine
from version import __version__

# parameters
settings = None
runran = True
ranprop = []
ranprop_lock = threading.Lock()

##########################
# the PRESENTCLOUD state #
##########################
class StatePRESENTCLOUD(State):
  def run(self, args):
    logging.debug(f"Entering {self.name}")
   
    # get the arguments
    try:
      ec: edgecomputer = args[0]
      cs: cloudorchestrator = args[1]

      # manage loads
      ec.hardware.cpus[0].load = psutil.cpu_percent() / 100
      g = 0
      for gpu in GPUtil.getGPUs():
        ec.hardware.gpus[g].load = None if isNaN(gpu.load) else gpu.load
        g += 1

    except:
      logging.error(f"{self.name} with missing arguments")
      return "DISCONNNECT", ("ACCEPTING")

    # amend with global information
    ranprop_lock.acquire()
    ec.ranprop = ranprop
    ranprop_lock.release()
    ec.mobiles = []
    try:
      if self.fsm.mobile:
        ec.mobiles.append(self.fsm.mobile)
    except:
      pass

    # prepare information
    message = {
      'subject': 'EDGE', 
      'ts': str(datetime.datetime.now()), 
      'access-key': cs.access_key, 
      'body': ec.to_dict()
    }
    
    # send information
    if liquidedge.sendMessage(self.fsm.csock, message):
      logging.debug("Presented to cloud")
    else:
      logging.warning("Not presented to cloud")

    # done
    logging.debug(f"Dismiss - Leaving {self.name}")      
    return "DISCONNECT", ()

#############################
# the CONTACTINGCLOUD state #
#############################
class StateCONTACTINGCLOUD(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")
    
    # creating the TCP/IP socket
    self.fsm.csock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.fsm.csock.setblocking(True)
    logging.debug("Opened TCP/IP socket")

    try:
      # connect to the cloud
      logging.info(f"Contacting the cloud orchestrator {self.fsm.cloud.address[0]}:{self.fsm.cloud.address[1]}")
      self.fsm.csock.settimeout(5) # timeout for connecting
      self.fsm.csock.connect(self.fsm.cloud.address)
      logging.debug("Cloud contacted")
      logging.debug(f"Leaving {self.name}")      
      return "PRESENTCLOUD", (self.fsm.edge, self.fsm.cloud)

    except:
      # no connection
      logging.error("Cloud orchestrator is not responding")
      self.fsm.csock.close()
      logging.debug(f"Timeout - Leaving {self.name}")       
      return "ACCEPTING", ()

####################
# the REFUSED case #
####################
class StateREFUSED(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get the arguments
      reason = args[0]

      # tell to go out
      logging.warning(reason)
      msg = {
        'subject': "REJECT", 
        'ts': str(datetime.datetime.now()),
        'body': {
          'reason': reason
        }
      }
      liquidedge.sendMessage(self.fsm.csock, msg)

    except:
      # error
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

    # done
    logging.debug(f"Dismiss - Leaving {self.name}")
    return "DISCONNECT", ()

# request a DNN file over a socket
def requestDNNFile(conn, cchfold, dnnfile):
  logging.info(f"Requesting {dnnfile['type']}")

  # request the object
  msg2 = {
    'subject': "SENDME",
    'ts': str(datetime.datetime.now()),
    'body': {
      'dnn-file': dnnfile
    }
  }
  if not liquidedge.sendMessage(conn, msg2):
    return False

  # dump data from socket to file
  dnnfile_path = os.path.join(cchfold, dnnfile['hash'])
  f = open(dnnfile_path, 'wb')
  while f.tell() < dnnfile['size']:
    try:
      chunk = conn.recv(1024) # split in chunks
      if not chunk:
        break
      f.write(chunk)
    except:
      break
  f.close()

  # find file hash
  m5h = liquidedge.generateFileMD5(dnnfile_path).upper()

  # check that the file is uncorrupted
  if os.path.getsize(dnnfile_path) != dnnfile['size'] or m5h != dnnfile['hash']:
    logging.error(f"Error receiving the {dnnfile['type']}")
    try:
      os.remove(dnnfile_path)
    except:
      pass
    return False
  else:
    logging.info(f"{dnnfile['type']} correctly received")
    return True

######################
# the GETFILES state #
######################
class StateGETFILES(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get the arguments
      msg = args[0]

      # request the files
      for df in msg['body']['files']:
        if not df['cached']:
          requestDNNFile(self.fsm.csock, self.fsm.fsm.edge.cachepath, df)

      logging.debug(f"Verify - Leaving {self.name}")
      return "CHECKCACHE", (msg,)

    except:
      # go out
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

#######################
# the STREAMNOW state #
#######################
class StateSTREAMNOW(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get the arguments
      msg = args[0]

      # random streaming key
      stream_key = ''.join(random.choices(string.ascii_letters + string.digits, k=8))

      # prepare the command
      cmd = {
        'subject': "STREAMNOW",
        'ts': str(datetime.datetime.now()),
        'body': {
          'address': (
            self.fsm.fsm.edge.address[0], 
            settings['edge']['streaming']['port'] 
          ),
          'stream-key': stream_key
        }
      }

      # send 
      if liquidedge.sendMessage(self.fsm.csock, cmd):
        # and start engine
        logging.info(f"Using {cmd['body']['address'][0]}:{cmd['body']['address'][1]} with key {cmd['body']['stream-key']}")

        # assoc mobile
        self.fsm.fsm.mobile = mobilenode.from_dict(msg['body'])

        # search through the engines
        for en in self.fsm.fsm.edge.engines:

          # same engine, same version
          if self.fsm.fsm.mobile.engine.name == en.name and self.fsm.fsm.mobile.engine.version == en.version:
            
            # no more available to others
            self.fsm.fsm.edge.avail = 0.0

            # we are streaming
            self.fsm.fsm.streaming = True

            # start the engine control thread
            inferencehandler = threading.Thread(target=self.fsm.handleInferenceEngine, args=(cmd['body'],en))
            inferencehandler.start()

            break

        if self.fsm.edge.avail == 1.0:

          # no engine matched
          logging.error("Mobile-requested DNN engine not available at the edge")

      # go out
      logging.debug(f"Done - Leaving {self.name}")
      return "DISCONNECT", ()

    except:
      # go out
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

#######################
# the CHECKCACHE case #
#######################
def get_folder_size(path = '.'):
  """Calculate the size of a folder"""
  size = 0
  for file_ in Path(path).rglob('*'):
    size += file_.stat().st_size
  return size

class StateCHECKCACHE(State):
  """Check the cache status"""
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get the arguments
      msg = args[0]

      # decide here whether the DNN files are needed or not
      wanted = False
      for i in range(len(msg['body']['files'])):
        msg['body']['files'][i]['cached'] = os.path.isfile(os.path.join(self.fsm.fsm.edge.cachepath, msg['body']['files'][i]['hash']))
        if msg['body']['files'][i]['cached']:
          logging.info(f"{msg['body']['files'][i]['name']} is cached")
        else:
          logging.info(f"{msg['body']['files'][i]['name']} is not cached")
        wanted = wanted or not msg['body']['files'][i]['cached']

      # update cache size
      self.fsm.fsm.edge.cachesize = get_folder_size(self.fsm.fsm.edge.cachepath)

      if wanted:
        logging.debug(f"Not cached - Leaving {self.name}")
        return "GETFILES", (msg,)
      else:
        logging.debug(f"Cached - Leaving {self.name}")
        if not self.fsm.fsm.streaming:
          return "STREAMNOW", (msg,)
        else:
          logging.debug(f"Error - Already streaming")
          return "DISCONNECT", ()

    except:
      # go out
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

###########################
# the AUTHORIZATION state #
###########################
class StateAUTHORIZATION(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get the arguments
      msg = args[0]

      # verify access key
      if msg['access-key'] != self.fsm.fsm.edge.access_key:
        logging.warning(f"Refused - Leaving {self.name}")
        return "REFUSED", ("subject_not_authorized", )

      # check the  subject
      if msg['subject'] == 'MOBILE':
        # a mobile
        logging.debug(f"Mobile '{msg['body']['name']}' authorized")
        logging.debug(f"Leaving {self.name}")
        return "CHECKCACHE", (msg,)

      elif msg['subject'] == 'CLOUD':
        # a cloud
        logging.debug(f"Cloud '{msg['body']['name']}' authorized")
        logging.debug(f"Leaving {self.name}")
        return "PRESENTCLOUD", (self.fsm.fsm.edge, self.fsm.fsm.cloud)

      else:
        # ?
        logging.warning(f"Refused - Leaving {self.name}")
        return "REFUSED", ("unknown_subject", )

    except:
      # some error occurred
      logging.error(f"{self.name} with error")
      logging.debug(f"Leaving {self.name}")
      return "DISCONNECT", ()

########################
# the DISCONNECT state #
########################
class StateDISCONNECT(State):
  outstate: str
  def __init__(self, name=None, ost=None):
    super().__init__(name)
    self.outstate = ost
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # close the incoming connection
    self.fsm.csock.close()

    # done
    logging.debug(f"Done - Leaving {self.name}")

    # should exit from FSM
    return self.outstate, ()

#######################
# the CONNECTED state #
#######################
class StateCONNECTED(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # receive something
    msg = liquidedge.receiveMessage(self.fsm.csock)
    if msg:
      # go on
      logging.info("Message")
      logging.debug("Received: " + str(msg))
      logging.debug(f"Presentation - Leaving {self.name}")
      return "AUTHORIZATION", (msg,)
    else:
      # go back
      logging.debug(f"Timeout - Leaving {self.name}")
      return "DISCONNECT", ()

#####################
# the CONNECTED FSM #
#####################
class FiniteStateMachineConnected(FiniteStateMachine):
  csock: socket
  fsm: FiniteStateMachineEdge
  caddr: tuple

  def __init__(self, name, sock, fsm, addr):
    # call base constructor
    super().__init__(name)
    self.fsm = fsm
    self.csock = sock
    self.caddr = addr

  def handleInferenceEngine(self, body, en):
    """Handles the inference engine - edge side"""

    # parse and replace the placeholders in the path
    replacedPath = []
    if en.path:

      # prepare the path
      for everypath in en.path:
        if everypath:
          singlePath = []
          for pathpiece in everypath:
            pathpiece = pathpiece.replace('__key__', body['stream-key'])
            pathpiece = pathpiece.replace('__address__', body['address'][0])
            pathpiece = pathpiece.replace('__port__', str(body['address'][1]))
            for f in self.fsm.mobile.files:
              ph = "__type" + f.type + "__"
              pathpiece = pathpiece.replace(ph, os.path.join(self.fsm.edge.cachepath, f.hash))
            singlePath.append(pathpiece)
          replacedPath.append(singlePath)
          #print(replacedPath)

      # launch the command
      try:

        if en.keepalive:

          # popen
          pp = []
          for rp in replacedPath:
            pp.append(subprocess.Popen(rp) if rp else None)
          
          # open a connection and monitor it
          liquidedge.keepalive_connection(pp, body['address'][0], body['address'][1], True, fsm=self)
          
          # connection is down, kill our processes
          for p in pp:
            p.kill()

        else:

          # run
          if replacedPath:
            for rp in replacedPath:
              #print("ECCO: " + " ".join(rp))
              subprocess.run(rp, check=True)

      except:

        pass

      # at least an engine ended or crashed - trigger a new edge assignment
      self.fsm.edge.avail = 1.0
      self.fsm.streaming = False
      self.fsm.mobile = None
      logging.error(f"Streaming at {body['address'][0]}:{body['address'][1]} with key {body['stream-key']} ended for some reason")

#######################
# the ACCEPTING state #
#######################
class StateACCEPTING(State):
  lastcloudtime: float
  def __init__(self, name: str = None):
    super().__init__(name)
    self.lastcloudtime = 0
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # accept connection
      csock, inaddr = self.fsm.asock.accept()
      logging.info(f"Incoming connection from {inaddr[0]}:{inaddr[1]}")

      # manage the connection
      connFsm = FiniteStateMachineConnected("CONNECTEDFSM", csock, self.fsm, inaddr)
      #time.sleep(1)

      # add states
      connFsm.addState(StateCONNECTED())
      connFsm.addState(StateDISCONNECT())
      connFsm.addState(StateAUTHORIZATION())
      connFsm.addState(StateREFUSED())
      connFsm.addState(StateCHECKCACHE())
      connFsm.addState(StateGETFILES())
      connFsm.addState(StateSTREAMNOW())
      connFsm.addState(StatePRESENTCLOUD())

      # run the machine directly
      connFsm.run()

      # done here
      logging.debug(f"Connection - Leaving {self.name}")
      return "ACCEPTING", ()

    except socket.timeout:
      #raise
      if time.monotonic() - self.lastcloudtime > settings['cloud']['interval']:
        # time to contact the cloud
        self.lastcloudtime = time.monotonic()
        logging.debug(f"Ack the cloud - Leaving {self.name}")
        return "CONTACTINGCLOUD", ()
      else:
        # nothing to do
        logging.debug(f"Timeout - Leaving {self.name}")
        return "ACCEPTING", ()

    except:
      # done
      logging.debug(f"Shutdown - Leaving {self.name}")
      return None, ()

################
# the EDGE FSM #
################
class FiniteStateMachineEdge(FiniteStateMachine):
  asock: socket
  cloud: cloudorchestrator
  csock: socket
  csock2: socket
  edge: edgecomputer
  mobile: mobilenode
  streaming: bool

  def __init__(self, name, ec, cs):
    # call base constructor
    super().__init__(name)
    self.edge = ec
    self.cloud = cs
    self.csock = None
    self.csock2 = None
    self.mobile = None
    self.streaming = False

    # creating the TCP/IP socket
    self.asock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.asock.settimeout(1) # timeout for listening
    logging.info("Opened TCP/IP socket")

    # binding the socket and listen for receiving data up to maxconn connections
    self.asock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.asock.bind(self.edge.address)
    self.asock.listen(settings['edge']['management']['max-connections'])
    logging.info(f"Listening on {self.edge.address[0]}:{self.edge.address[1]}")

#########################
# RAN management thread #
#########################
# populate object from response, parsing line after line
def response2object(resp, obj):
  n = 1
  for line in resp.splitlines():
    if line:
      #print(n, "=", line)
      toks = line.strip().split(": ")
      #print(toks[0], "=", toks[1])
      key = toks[0]
      if len(toks) == 2:
        val = toks[1]
      else:
        val = None
      obj[key] = val
    n += 1

def response2object2(resp, obj):
  splitresp = resp.split()
  n = 1
  for pair in splitresp:
    toks = pair.split("=")
    #print(toks)
    if len(toks) == 2:
      obj[toks[0]] = toks[1]


# convert bitrate to number
def bitrate2int( brstr ):
  if 'Gbps' in brstr:
    return int(1e9 * float(brstr.replace('Gbps', '')))
  elif 'Mbps' in brstr:
    return int(1e6 * float(brstr.replace('Mbps', '')))
  else:
    return None

# the thread
def ranEngine(ran):
  """Handles the RAN management - edge side"""
  global ranprop
  
  logging.info(f"RAN engine started on AP {ran['address']}")

  # instantiate the SSH object
  ssh_client = paramiko.SSHClient()

  # assume host is known
  ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

  while runran:

    # initial values of our metric
    obj = {
      'ip-address': ran['address'],
      'utc-ts': None,
      'connected': None,
      'coordinates': {
        'x': ran['coordinates']['x'], 
        'y': ran['coordinates']['y']
      },
      'name': None,
      'frequency': None, 
      'remote-address': [],
      'mac-address': None,
      'tx-mcs': [],
      'tx-phy-rate': [],
      'signal': [],
      'rssi': [],
      'tx-sector': [],
      'distance': [],
      'tx-packet-error-rate': None
    }
    
    # assign to global
    ranprop_lock.acquire()
    ranprop = obj
    ranprop_lock.release()

    try:
      # connection
      ssh_client.connect(obj['ip-address'], username='admin', password="", timeout=1)

      # identity
      stdin, stdout, stderr = ssh_client.exec_command('/system identity print')
      response = stdout.read().decode("utf-8")

      # populate object
      response2object(response, obj)
      apname = obj['name']
      #print(obj)

      # MAC address
      stdin, stdout, stderr = ssh_client.exec_command('/interface w60g print detail')
      response = stdout.read().decode("utf-8")
      response2object2(response, obj)

      while runran:

        # check the AP
        logging.info(f"Checking AP {ran['address']}")

        # reset object fields
        obj['utc-ts'], obj['connected'], obj['frequency'], obj['tx-packet-error-rate'] = (None, ) * 4
        obj['remote-address'], obj['tx-mcs'], obj['tx-phy-rate'], obj['signal'], obj['rssi'], obj['tx-sector'], obj['distance'] = ([], ) * 7

        # status of wireless interface
        stdin, stdout, stderr = ssh_client.exec_command('/interface w60g monitor wlan60-1 once')
        response = stdout.read().decode("utf-8")

        # current timestamp
        obj['utc-ts'] = str(datetime.datetime.utcnow())

        # populate object
        response2object(response, obj)
        obj['name'] = apname

        # fix values
        try:
          obj['remote-address'] = obj['remote-address'].split(',')
        except:
          pass
        try:
          obj['default-scan-list'] = obj['default-scan-list'].split(',')
        except:
          pass
        try:
          obj['tx-mcs'] = list(map(int, obj['tx-mcs'].split(',')))
        except:
          pass
        try:
          obj['tx-phy-rate'] = [ bitrate2int(i) for i in obj['tx-phy-rate'].split(',') ]
        except:
          pass
        try:
          obj['signal'] = list(map(int, obj['signal'].split(',')))
        except:
          pass
        try:
          obj['rssi'] = list(map(int, obj['rssi'].split(',')))
        except:
          pass
        try:
          obj['tx-sector'] = list(map(int, obj['tx-sector'].split(',')))
        except:
          pass
        try:
          obj['distance'] = [ float(i.replace('m', '')) for i in obj['distance'].split(',') ] 
        except:
          pass
        try:
          del obj['tx-sector-info']
        except:
          pass
        #print(obj)

        # assign to global
        ranprop_lock.acquire()
        ranprop = obj
        ranprop_lock.release()

        # sleep a little
        time.sleep(1)

      # shutdown the connection
      ssh_client.close()

    except:

      # device is not connected
      logging.error(f"RAN AP {ran['address']} not connected")
      obj['utc-ts'] = str(datetime.datetime.utcnow())
      obj['connected'] = False

      # sleep a little
      time.sleep(1)

  logging.info(f"RAN engine stopped on AP {ran['address']}")


#################
# MAIN function #
#################
def main():
  # start logging
  logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='[%(levelname)s] %(asctime)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
  logging.addLevelName(logging.WARNING, "\033[1;33m%s\033[1;0m" % logging.getLevelName(logging.WARNING))
  logging.addLevelName(logging.ERROR, "\033[1;31m%s\033[1;0m" % logging.getLevelName(logging.ERROR))

  # parser
  parser = argparse.ArgumentParser(description="\033[1;1;32mLIQUID\033[1;1;33m⠪EDGE\033[1;0m edge computer agent")
  parser.add_argument("-V", '--version', help="show agent version and exit", action='version', version='%(prog)s ' + __version__)
  parser.add_argument("-c", "--config", help="edge configuration YAML file", default="./edge_config.yaml")
  args = parser.parse_args()

  # load settings file
  try:
    with open(args.config) as f:
      global settings
      settings = yaml.load(f, Loader=yaml.FullLoader)
      logging.debug("Settings: " + str(settings))
  except:
    logging.error("Configuration file not found")
    sys.exit()

  # create cache folder
  local_cache_folder = os.path.join(tempfile.gettempdir(), "le_edge_cache") if settings['edge']['cache']['folder'] == "auto" else settings['edge']['cache']['folder']
  if not os.path.exists(local_cache_folder):
    logging.info(f"Created local cache folder '{local_cache_folder}'")
    os.makedirs(local_cache_folder)

  # start parallel process
  if 'parallel' in settings['edge'] and settings['edge']['parallel']:
    try:
      subprocess.Popen(settings['edge']['parallel'])
    except:
      logging.error(f"Could not start parallel '{settings['edge']['parallel']}'")
      sys.exit()

  # create local edge
  myec = edgecomputer((platform.node() + ".edgecomputer") if settings['edge']['name'] == "auto" else settings['edge']['name'])
  myec.address = (settings['edge']['management']['address'], settings['edge']['management']['port'])
  myec.cachepath = local_cache_folder
  myec.access_key = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
  myec.uuid = uuid.UUID(str(uuid.getnode()).rjust(32, '0'))
  myec.hardware.cpus.append(pu(model=' '.join(cpuinfo.get_cpu_info()['brand_raw'].split()), arch=platform.machine()))
  for gpu in GPUtil.getGPUs():
    myec.hardware.gpus.append(pu(model=gpu.name))
  for es in settings['edge']['engines']:
    myec.engines.append(engine(name=es['name'], version=es['version'], path=es['path'], keepalive=es['keepalive'], viewport=es['viewport']))

  # create remote cloud
  mycs = cloudorchestrator()
  mycs.address = (settings['cloud']['address'], settings['cloud']['port'])
  mycs.access_key = settings['cloud']['access-key']

  # start RAN monitoring thread
  ranhandler = threading.Thread(target=ranEngine, args=(settings['edge']['ran'],))
  ranhandler.start()

  # create local machine
  edgeFsm = FiniteStateMachineEdge("CalcolatoreAlBordo", myec, mycs)

  # add states
  edgeFsm.addState(StateACCEPTING())
  edgeFsm.addState(StateCONTACTINGCLOUD())
  edgeFsm.addState(StatePRESENTCLOUD())
  edgeFsm.addState(StateDISCONNECT(ost="ACCEPTING"))

  # run the machine
  edgeFsm.run()

  # stop the thread
  global runran
  runran = False

  # report
  #edgeFsm.summary()

######################
# SCRIPT starts here #
######################
if __name__ == "__main__":
  main()


