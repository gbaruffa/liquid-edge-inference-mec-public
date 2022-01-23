#!/usr/bin/python3

# import section
import argparse
import datetime
import logging
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import uuid
import yaml
import cpuinfo
import paramiko
import io
import pynmea2
import serial

import liquidedge
from liquidedge import edgecomputer, mobilenode, cloudorchestrator, engine, hwm, dnnfile, pu
from littlefsm import State, FiniteStateMachine
from version import __version__

# parameters
settings = None
runran = True
ranprop = []
ranprop_lock = threading.Lock()

#######################
# the SENDFILES state #
#######################
class StateSENDFILES(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    df = args[1]['body']['dnn-file']
    logging.info(f"Sending a file to {args[0]}: {df['name']} - {df['hash']}")

    # find the file to send
    for f in self.fsm.mobile.files:
      if f.hash == df['hash']:
        # send the file in chunks
        with open(f.path, "rb") as ff:
          while True:
            data = ff.read(4096)  # read the bytes from the file
            if not data:
              break  # file transmission is done
            # send the chunk
            self.fsm.csock.sendall(data) # we use sendall to ensure transmission in busy networks
          break

    logging.debug(f" - Leaving {self.name}")
    return "COMMAND", args[0]

########################
# the DISCONNECT state #
########################
class StateDISCONNECT(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # close socket
    self.fsm.csock.close()

    # done
    logging.debug(f"Disconnection - Leaving {self.name}")  
    return "MAINLOOP", ()

#######################
# the STREAMNOW state #
#######################
class StateSTREAMNOW(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")
    logging.info(f"Edge to use is {args[1]['body']['address'][0]}:{args[1]['body']['address'][1]} with key {args[1]['body']['stream-key']}")

    # start the engine control thread
    inferencehandler = threading.Thread(target=self.fsm.handleInferenceEngine, args=(args[1]['body'],))
    inferencehandler.start()

    # go back to command
    logging.debug(f"Ok - Leaving {self.name}")
    return "COMMAND", args[0]

#####################
# the USEEDGE state #
#####################
class StateUSEEDGE(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # assign the edge
    self.fsm.edge = edgecomputer.from_dict(args[1]['body'])
    
    # go back to command
    logging.debug(f"Ok - Leaving {self.name}")
    return "COMMAND", args[0]

#####################
# the COMMAND state #
#####################
class StateCOMMAND(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")
    
    # receive a command
    cmd = liquidedge.receiveMessage(self.fsm.csock)

    if cmd:
      # there is a command
      logging.debug("Received: " + str(cmd))

      try:
        # need to send the files
        if cmd['subject'] == "SENDME":
          # gotta send the files
          logging.info(f"{args} wants the files")
          logging.debug(f"Files requested - Leaving {self.name}")
          return "SENDFILES", (args, cmd)

        elif cmd['subject'] == "USEEDGE":
          # the cloud assigns us an edge
          logging.debug(f"Assign edge - Leaving {self.name}")
          return "USEEDGE", (args, cmd)

        elif cmd['subject'] == "STREAMNOW":
          # the edge wants us to stream
          logging.info("Streaming request")
          logging.debug(f"Start streaming - Leaving {self.name}")
          return "STREAMNOW", (args, cmd)

        elif cmd['subject'] == "BYE":
          # hello
          logging.debug(f"Done - Leaving {self.name}")
          return "DISCONNECT", ()

        elif cmd['subject'] == "REJECT":
          # wrong key? retry
          time.sleep(1)
          self.fsm.edge = None
          logging.debug(f"Rejected - Leaving {self.name}")
          return "DISCONNECT", ()

      except:
        logging.debug(f"Bad protocol - Leaving {self.name}")  
        return "DISCONNECT", ()

    else:
      # the other side has disconnected or some other error
      logging.debug(f"Timeout - Leaving {self.name}")
      return "DISCONNECT", ()

#####################
# the PRESENT state #
#####################
class StatePRESENT(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # destination
      destin = args[0]

      # access
      if destin == "Cloud":
        access_key = settings['cloud']['access-key']
      elif destin == "Edge" and self.fsm.edge:
        access_key = self.fsm.edge.access_key
    
      # amend with global information
      ranprop_lock.acquire()
      self.fsm.mobile.ranprop = ranprop
      ranprop_lock.release()

      # prepare our information
      message = {
        'subject': 'MOBILE', 
        'ts': str(datetime.datetime.now()), 
        'access-key': access_key, 
        'body': self.fsm.mobile.to_dict(),
        'streaming': self.fsm.streaming
      }

      # send the information
      if liquidedge.sendMessage(self.fsm.csock, message):
        logging.debug(f"Presenting ourselves to {destin}")
        if self.fsm.streaming:
          # streaming is operating correctly
          logging.debug(f"Streaming on - Leaving {self.name}")      
          return "DISCONNECT", ()
        else:
          # expecting some command
          logging.debug(f"Wait commands - Leaving {self.name}")      
          return "COMMAND", (destin,)
      else:
        logging.debug(f"Timeout - Leaving {self.name}")     
        return "DISCONNECT", ()

    except:
      # going nowhere
      logging.error(f"Some error in {self.name}")
      logging.debug(f"Error - Leaving {self.name}")      
      return "DISCONNECT", ()

########################
# the CONTACTING state #
########################
class StateCONTACTING(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # destination
      destin = args[0]

      if destin == "Cloud":
        # going to the cloud
        address = self.fsm.cloud.address
      elif destin == "Edge":
        # going to the edge
        address = self.fsm.edge.address
      else:
        # going nowhere
        logging.error(f"{destin} is unknown")
        logging.debug(f"Error - Leaving {self.name}")      
        return "MAINLOOP", ()

      try:
        # creating the TCP/IP socket
        self.fsm.csock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.fsm.csock.setblocking(True)
        logging.debug("Opened TCP/IP socket")

        # connect
        logging.info(f"Contacting the {destin} at {address[0]}:{address[1]}")
        self.fsm.csock.settimeout(5) # timeout for connecting
        self.fsm.csock.connect(address)
        logging.debug(f"{destin} contacted")
        logging.debug(f"Leaving {self.name}")      
        return "PRESENT", (destin,)

      except:
        logging.error(f"{destin} is not responding")
        if destin == "Edge":
          self.fsm.edge = None
        logging.debug(f"Timeout - Leaving {self.name}")      
        return "MAINLOOP", ()

    except:
      # going nowhere
      logging.error(f"Some error in {self.name}")
      logging.debug(f"Error - Leaving {self.name}")      
      return "MAINLOOP", ()

#########################
# the SETUPSTREAM state #
#########################
class StateSETUPSTREAM(State):
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    if self.fsm.edge:
      # I have an edge, contact the edge
      logging.debug(f"Edge assigned - Leaving {self.name}")      
      return "CONTACTING", ("Edge",)

    else:
      # I do not have an edge, contact the cloud
      logging.debug(f"Edge not assigned - Leaving {self.name}")      
      return "CONTACTING", ("Cloud",)

######################
# the MAINLOOP state #
######################
class StateMAINLOOP(State):
  lastcloudtime: float

  def __init__(self, name: str = None):
    super().__init__(name)
    self.lastcloudtime = 0
    
  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # throttle delay
    time.sleep(1) 

    if self.fsm.streaming:
      # streaming is operating correctly

      if time.monotonic() - self.lastcloudtime > settings['cloud']['interval']:

        # time to contact the cloud
        self.lastcloudtime = time.monotonic()
        logging.debug(f"Ack the cloud - Leaving {self.name}")
        return "CONTACTING", ("Cloud", )

      else:
        logging.debug(f"Streaming on - Leaving {self.name}")      
        return "MAINLOOP", ()

    else:
      # no streaming for some reason
      logging.debug(f"Streaming off - Leaving {self.name}")      
      return "SETUPSTREAM", ()

###################################
class FiniteStateMachineMobile(FiniteStateMachine):
  """A finite state machine for a mobile node"""

  csock: socket
  mobile: mobilenode
  cloud: cloudorchestrator
  edge: edgecomputer
  streaming: bool

  def __init__(self, name: str, mn: mobilenode, cs: cloudorchestrator):
    """Construct an FSM for a mobile node"""

    # call the base constructor
    super().__init__(name)
    self.csock = None
    self.mobile = mn
    self.cloud = cs
    self.edge = None
    self.streaming = False

  def handleInferenceEngine(self, body):
    """Handles the inference engine - mobile side"""

    # spawn the child engine process
    try:

      # parse and replace the placeholders in the path
      replacedPath = []
      if self.mobile.engine.path:
        for pathpiece in self.mobile.engine.path:
          pathpiece = pathpiece.replace('__key__', body['stream-key'])
          pathpiece = pathpiece.replace('__address__', body['address'][0])
          pathpiece = pathpiece.replace('__port__', str(body['address'][1]))
          replacedPath.append(pathpiece)
        #print(replacedPath)

      # we are streaming
      self.streaming = True  

      if not self.mobile.engine.keepalive:

        # run directly
        if replacedPath:
          #print("ECCO: " + " ".join(replacedPath))
          subprocess.run(replacedPath, check=True)

      else:

        # popen
        p = subprocess.Popen(replacedPath) if replacedPath else None
        
        # open a connection and monitor it
        time.sleep(1.5) # give time to popen call on the other side
        liquidedge.keepalive_connection(p, addrm=body['address'][0], portm=body['address'][1], fsm=self)
        
        # connection is down, kill our process
        if p:
          p.kill()

    except:
      pass
    
    # problem - trigger a new edge assignment
    self.streaming = False  # we are not streaming anymore
    logging.error(f"Streaming to {body['address'][0]}:{body['address'][1]} with key {body['stream-key']} ended for some reason")

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

# the RAN thread
def ranEngine(ran):
  """Handles the RAN management - mobile side"""
  global ranprop
  
  logging.info(f"RAN engine started on STAs {ran['addresses']}")

  objs = [None] * len(ran['addresses'])
  ssh_clients = [None] * len(ran['addresses'])
  for i in range(len(objs)):

    # instantiate the SSH object
    ssh_clients[i] = paramiko.SSHClient()

    # assume host is known
    ssh_clients[i].set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # initial values of our metric
    objs[i] = {
      'ip-address': ran['addresses'][i],
      'utc-ts': None,
      'connected': None,
      'name': None,
      'frequency': None, 
      'remote-address': None,
      'mac-address': None,
      'tx-mcs': None,
      'tx-phy-rate': None,
      'signal': None,
      'rssi': None,
      'tx-sector': None,
      'distance': None,
      'tx-packet-error-rate': None
    }

    # assign to global
    ranprop_lock.acquire()
    ranprop = objs
    ranprop_lock.release()

    try:

      # test connection
      # try:
      #   transport = ssh_clients[i].get_transport()
      #   transport.send_ignore()
      # except:

      # connection is closed, open it
      ssh_clients[i].connect(objs[i]['ip-address'], username='admin', password="", timeout=1)

      # identity
      stdin, stdout, stderr = ssh_clients[i].exec_command('/system identity print')
      response = stdout.read().decode("utf-8")

      # populate object
      response2object(response, objs[i])
      staname = objs[i]['name']

      # MAC address
      stdin, stdout, stderr = ssh_clients[i].exec_command('/interface w60g print detail')
      response = stdout.read().decode("utf-8")
      response2object2(response, objs[i])
      objs[i]['name'] = staname
      #print(obj)

      ssh_clients[i].close()

    except:

      # device is not connected
      logging.error(f"RAN STA {ran['addresses'][i]} not connected")
      objs[i]['utc-ts'] = str(datetime.datetime.utcnow())
      objs[i]['connected'] = False

  while runran:
  
    for i in range(len(objs)):

      #print("FOR CYCLE")

      try:

        # # test connection
        # try:
        #   print("CHECK TRANSPORT")
        #   transport = ssh_clients[i].get_transport()
        #   transport.send_ignore()
        # except:

        # connection is closed, open it
        #print("CONNECT")
        ssh_clients[i].connect(objs[i]['ip-address'], username='admin', password="", timeout=1)

        # check the AP
        logging.info(f"Checking STA {ran['addresses'][i]}")

        # reset object fields
        objs[i]['utc-ts'], objs[i]['connected'], objs[i]['frequency'], objs[i]['remote-address'], objs[i]['tx-mcs'], objs[i]['tx-phy-rate'], objs[i]['signal'], objs[i]['rssi'], objs[i]['tx-sector'], objs[i]['distance'], objs[i]['tx-packet-error-rate'] = (None, ) * 11

        # status of wireless interface
        stdin, stdout, stderr = ssh_clients[i].exec_command('/interface w60g monitor wlan60-1 once')
        response = stdout.read().decode("utf-8")

        # current timestamp
        objs[i]['utc-ts'] = str(datetime.datetime.utcnow())

        # populate object
        response2object(response, objs[i])

        # fix values
        try:
          objs[i]['default-scan-list'] = objs[i]['default-scan-list'].split(',')
        except:
          pass
        try:
          del objs[i]['tx-sector-info']
        except:
          pass
        try:
          objs[i]['distance'] = float(objs[i]['distance'].replace('m', ''))
        except:
          pass

        #print(obj)

        ssh_clients[i].close()

      except:

        # device is not connected
        logging.error(f"RAN STA {ran['addresses'][i]} not connected")
        objs[i]['utc-ts'] = str(datetime.datetime.utcnow())
        objs[i]['connected'] = False

    # assign to global
    ranprop_lock.acquire()
    ranprop = objs
    ranprop_lock.release()
    
    # sleep a little
    time.sleep(1)

  # # shutdown the connections
  # for ssh_client in ssh_clients:
  #   ssh_client.close()

  logging.info(f"RAN engine stopped on STAs {ran['addresses']}")


# the GPS thread
def gpsEngine(mn: mobilenode):
  """Handles the GPS management - mobile side"""

  logging.info(f"GPS engine started")

  # open the serial port
  try:
    ser = serial.Serial(mn.serialport, 9600, timeout=5.0)
  except serial.SerialException as e:
    logging.error('GPS device: {}'.format(e))
    return

  sio = io.TextIOWrapper(io.BufferedRWPair(ser, ser))

  while runran:
    try:
      # read a whole line from serial
      line = sio.readline()

      # parse the NMEA message
      msg = pynmea2.parse(line)

      # get the position
      if msg.sentence_type == "GGA":
        mn.coordinates = (msg.latitude, msg.longitude)
        #logging.info(repr(msg))
        logging.info("LAT: " + str(msg.latitude) + ", LNG: " + str(msg.longitude))

    except serial.SerialException as e:
      logging.error('Device: {}'.format(e))
      break

    except pynmea2.ParseError as e:
      logging.error('Parse: {}'.format(e))
      continue

  logging.info(f"GPS engine stopped")


#################
def main():
  """The main function"""

  # start logging
  logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='[%(levelname)s] %(asctime)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
  logging.addLevelName(logging.WARNING, "\033[1;33m%s\033[1;0m" % logging.getLevelName(logging.WARNING))
  logging.addLevelName(logging.ERROR, "\033[1;31m%s\033[1;0m" % logging.getLevelName(logging.ERROR))

  # parser
  parser = argparse.ArgumentParser(description="\033[1;1;32mLIQUID\033[1;1;33m⠪EDGE\033[1;0m mobile node agent")
  parser.add_argument("-V", '--version', help="show agent version and exit", action='version', version='%(prog)s ' + __version__)
  parser.add_argument("-c", "--config", help="mobile configuration YAML file", default="./mobile_config.yaml")
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

  # create local mobile
  mymn = mobilenode((platform.node() + ".mobilenode") if settings['mobile']['name'] == "auto" else settings['mobile']['name'])
  mymn.address = tuple()
  mymn.coordinates = (settings['mobile']['coordinates']['x'], settings['mobile']['coordinates']['y'])
  mymn.serialport = settings['mobile']['serialport']
  mymn.uuid = uuid.UUID(str(uuid.getnode()).rjust(32, '0'))
  mymn.hardware = hwm(settings['mobile']['robot']['name'])
  mymn.hardware.cpus.append(pu(' '.join(cpuinfo.get_cpu_info()['brand_raw'].split()), platform.machine()))
  mymn.engine = engine(name=settings['mobile']['engine']['name'], version=settings['mobile']['engine']['version'], path=settings['mobile']['engine']['path'], keepalive=settings['mobile']['engine']['keepalive'])
  mymn.files = []
  for fp in settings['mobile']['files']:
    try:
      mymn.files.append(dnnfile(fp['type'], os.path.basename(fp['path']), fp['path'], os.path.getsize(fp['path']), liquidedge.generateFileMD5(fp['path']).upper()))
    except:
      logging.error(f"Missing {fp['type']} file")

  # start parallel process
  if 'parallel' in settings['mobile'] and settings['mobile']['parallel']:
    try:
      subprocess.Popen(settings['mobile']['parallel'])
    except:
      logging.error(f"Could not start parallel '{settings['mobile']['parallel']}'")
      sys.exit()

  # create remote cloud
  mycs = cloudorchestrator()
  mycs.address = (settings['cloud']['address'], settings['cloud']['port'])
  mycs.access_key = settings['cloud']['access-key']

  # start RAN monitoring thread
  ranhandler = threading.Thread(target=ranEngine, args=(settings['mobile']['ran'],))
  ranhandler.start()

  # start GPS monitoring thread
  gpshandler = threading.Thread(target=gpsEngine, args=(mymn,))
  gpshandler.start()

  # create the local machine
  mobileFsm = FiniteStateMachineMobile("NodoMobile", mymn, mycs)
  
  # add all the states
  mobileFsm.addState(StateMAINLOOP())
  mobileFsm.addState(StateSETUPSTREAM())
  mobileFsm.addState(StateCONTACTING())
  mobileFsm.addState(StatePRESENT())
  mobileFsm.addState(StateCOMMAND())
  mobileFsm.addState(StateUSEEDGE())
  mobileFsm.addState(StateSTREAMNOW())
  mobileFsm.addState(StateSENDFILES())
  mobileFsm.addState(StateDISCONNECT())

  # run the machine
  mobileFsm.run()

  # stop the thread
  global runran
  runran = False

  # report
  #mobileFsm.summary()

######################
# SCRIPT starts here #
######################
if __name__ == "__main__":
  main()

