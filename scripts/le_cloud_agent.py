#!/usr/bin/python3

# import section
import argparse
import datetime
import logging
import os
import platform
import socket
import sys
import tempfile
import time
import uuid
import yaml
import subprocess
import threading

import liquidedge
from liquidedge import edgecomputer, mobilenode, cloudorchestrator, dnnfile
from littlefsm import State, FiniteStateMachine
from version import __version__
from flask import Flask, request, render_template, jsonify

# parameters
settings = None
edgelist_lock = threading.Lock()

#################
def requestDNNFile(conn: socket, cchfold: str, df: dnnfile):
  """Request a DNN file over a socket."""

  logging.info(f"Requesting {df.type}")

  # request the object
  msg2 = {
    'subject': "SENDME",
    'ts': str(datetime.datetime.now()),
    'body': {
      'dnn-file': df.to_dict()
    }
  }
  if not liquidedge.sendMessage(conn, msg2):
    return False

  # dump data from socket to file
  local_df_path = os.path.join(cchfold, df.hash)
  f = open(local_df_path, 'wb')
  while f.tell() < df.size:
    try:
      chunk = conn.recv(1024) # split in chunks
      if not chunk:
        break
      f.write(chunk)
    except:
      break
  f.close()

  # find file hash
  m5h = liquidedge.generateFileMD5(local_df_path).upper()

  # check that the file is uncorrupted
  if os.path.getsize(local_df_path) != df.size or m5h != df.hash:
    logging.error(f"Error receiving the {df.type}")
    try:
      os.remove(local_df_path)
    except:
      pass
    return False
  else:
    logging.info(f"{df.type} correctly received")
    return True

####################
class StateREFUSED(State):
  """The REFUSED case."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # go out
    logging.warning(args[0])
    msg2 = {
      'subject': "REJECT", 
      'ts': str(datetime.datetime.now()),
      'body': {
        'reason': args[0]
      }
    }
    liquidedge.sendMessage(self.fsm.csock, msg2)
    logging.debug(f"Dismiss - Leaving {self.name}")
    return "DISCONNECT", ()

#######################
class StateEDGEFOUND(State):
  """The EDGEFOUND state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      ec = args[0]
    except:
      logging.error(f"Problems in {self.name}")
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

    # edge information
    msg2 = {
      'subject': "USEEDGE", 
      'ts': str(datetime.datetime.now()),
      'body': ec.to_dict()
    }
    liquidedge.sendMessage(self.fsm.csock, msg2)
    
    logging.debug(f"Send edge information - Leaving {self.name}")
    return "DISCONNECT", ()

#########################
class StatePRESENTEDGE(State):
  """The PRESENTEDGE state"""

  def run(self, args):
    logging.debug(f"Entering {self.name}")

    try:
      _mn = args[0]
      ec = args[1]
    except:
      # some problem
      logging.error("Problems during presentation to the edge")
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()
    
    # prepare information
    message = {
      'subject': 'CLOUD', 
      'ts': str(datetime.datetime.now()), 
      'access-key': ec.access_key, 
      'body': self.fsm.cloud.to_dict()
    }

    # send information
    if liquidedge.sendMessage(self.fsm.csock2, message):
      logging.info("Presented to edge")

      # get back some updated information
      msg2 = liquidedge.receiveMessage(self.fsm.csock2)

      try:
        # populate the edge
        ecn = edgecomputer.from_dict(msg2['body'])
        if ecn.avail > 0.75:
          return "EDGEFOUND", (ecn,)
      
      except:
        pass
      
    else:
      logging.warning("Not presented to edge")
    self.fsm.csock2.close()  # done with the socket

    # done
    logging.debug(f"Done - Leaving {self.name}")      
    return "DISCONNECT", ()

############################
class StateCONTACTINGEDGE(State):
  """The CONTACTINGEDGE state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get parameters
      mn = args[0]
      ec = args[1]

    except:
      # some problem
      logging.error("Problems during contact of an edge")
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

    # creating the TCP/IP socket
    self.fsm.csock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.fsm.csock2.setblocking(True)
    logging.debug("Opened TCP/IP socket")

    try:
      # connect to the edge
      logging.info(f"Contacting the edge computer {ec.address[0]}:{ec.address[1]}")
      self.fsm.csock2.settimeout(5) # timeout for connecting
      self.fsm.csock2.connect(ec.address)
      logging.info("Edge contacted")
      logging.debug(f"Edge contacted - Leaving {self.name}")      
      return "PRESENTEDGE", (mn, ec)

    except:
      # no connection
      logging.error("Edge computer is not responding")
      logging.debug(f"Timeout - Leaving {self.name}")   
      time.sleep(1)    
      return "FINDEDGE", (mn, ec)

######################
class StateFINDEDGE(State):
  """The FINDEDGE state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get argument
      mn = args[0]
      
      try:
        ec = args[1]
      except:
        ec = None

    except:
      # some problem
      logging.error("Problems during search of an edge")
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

    # propose an edge
    if not ec:
      edgelist_lock.acquire()
      ec = self.fsm.cloud.matchEdgeComputer(mn)
      edgelist_lock.release()

    if ec:
      # there is an edge and a mobile
      logging.debug("Edge proposed: " + str(ec.to_dict()))
      logging.debug(f"Edge candidate - Leaving {self.name}")
      return "CONTACTINGEDGE", (mn, ec)
    else:
      logging.info("No edge found")
      logging.debug(f"No edge available - Leaving {self.name}")
      return "REFUSED", ("edge_not_available",)

######################
class StateGETFILES(State):
  """The GETFILES state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      mn = args[0]
    except:
      # some problem
      logging.error("Problems during file get")
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

    # request the files
    for df in mn.files:
      if not df.cached:
        requestDNNFile(self.fsm.csock, self.fsm.cloud.cachepath, df)

    logging.debug(f"Verify - Leaving {self.name}")
    return "CHECKCACHE", (mn,)

#######################
class StateCHECKCACHE(State):
  """The CHECKCACHE case."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # get arguments
      mn = args[0]

      # decide here whether the DNN files are needed or not
      wanted = False
      for i in range(len(mn.files)):
        mn.files[i].cached = os.path.isfile(os.path.join(self.fsm.cloud.cachepath, mn.files[i].hash))
        if mn.files[i].cached:
          logging.info(f"{mn.files[i].name} is cached")
        else:
          logging.info(f"{mn.files[i].name} is not cached")
        wanted = wanted or not mn.files[i].cached

      if wanted:
        logging.debug(f"Not cached - Leaving {self.name}")
        return "GETFILES", args
      else:
        logging.debug(f"Cached - Leaving {self.name}")
        return "FINDEDGE", (mn,)

    except:
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

######################
class StateEDGELIST(State):
  """The EDGELIST state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # get the arguments
    try:
      msg = args[0]
    except:
      logging.error(f"{self.name} with missing argument")
      return "DISCONNECT", ()

    # reimport edge string
    ec = edgecomputer.from_dict(msg['body'])
    if not ec:
      logging.error(f"{self.name} bad edge format")
      return "DISCONNECT", ()

    # search the edge
    edgelist_lock.acquire()
    found = False
    for e in range(len(self.fsm.cloud.edges)):
      logging.debug(f"Edge #{e + 1}: {self.fsm.cloud.edges[e].to_dict()}")
      if self.fsm.cloud.edges[e].uuid == ec.uuid:
        # existing edge
        self.fsm.cloud.edges[e] = ec
        self.fsm.cloud.edges[e].lastts = datetime.datetime.strptime(msg['ts'], '%Y-%m-%d %H:%M:%S.%f')
        logging.info(f"Edge '{ec.name}' updated")
        found = True
    edgelist_lock.release()
    
    # new edge
    if not found:
      edgelist_lock.acquire()
      self.fsm.cloud.edges.append(ec)
      self.fsm.cloud.edges[-1].lastts = datetime.datetime.strptime(msg['ts'], '%Y-%m-%d %H:%M:%S.%f')
      edgelist_lock.release()
      logging.info(f"Edge '{ec.name}' inserted")

    logging.debug(f"List updated - Leaving {self.name}")
    return "DISCONNECT", ()

######################
class StateMOBILELIST(State):
  """The MOBILELIST state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # get the arguments
    try:
      msg = args[0]
    except:
      logging.error(f"{self.name} with missing argument")
      return "DISCONNECT", ()

    # reimport mobile string
    mn = mobilenode.from_dict(msg['body'])
    if not mn:
      logging.error(f"{self.name} bad mobile format")
      return "DISCONNECT", ()

    # search the mobile
    found = False
    for m in range(len(self.fsm.cloud.mobiles)):
      logging.debug(f"Mobile #{m + 1}: {self.fsm.cloud.mobiles[m].to_dict()}")
      if self.fsm.cloud.mobiles[m].uuid == mn.uuid:
        # existing mobile
        self.fsm.cloud.mobiles[m] = mn
        self.fsm.cloud.mobiles[m].lastts = datetime.datetime.strptime(msg['ts'], '%Y-%m-%d %H:%M:%S.%f')
        logging.info(f"Mobile '{mn.name}' updated")
        found = True
    
    # new mobile
    if not found:
      self.fsm.cloud.mobiles.append(mn)
      self.fsm.cloud.mobiles[-1].lastts = datetime.datetime.strptime(msg['ts'], '%Y-%m-%d %H:%M:%S.%f')
      logging.info(f"Mobile '{mn.name}' inserted")

    logging.debug(f"List updated - Leaving {self.name}")
    return "DISCONNECT", ()

###########################
class StateAUTHORIZATION(State):
  """The AUTHORIZATION state."""

  def run(self, args):

    # begin
    logging.debug(f"Entering {self.name}")

    try:

      # check the access key and the subject
      msg = args[0]
      if msg['subject'] == 'MOBILE' and msg['access-key'] == settings['cloud']['management']['access-key']:

        # the mobile called
        mn = mobilenode.from_dict(msg['body'])
        logging.debug(f"Mobile '{mn.name}' authorized")
        logging.debug(f"Leaving {self.name}")

        if msg['streaming']:
          # simple ACK
          return "MOBILELIST", args
        else:
          # search the mobile
          found = False
          for m in range(len(self.fsm.cloud.mobiles)):
            logging.debug(f"Mobile #{m + 1}: {self.fsm.cloud.mobiles[m].to_dict()}")
            if self.fsm.cloud.mobiles[m].uuid == mn.uuid:
              # existing mobile
              self.fsm.cloud.mobiles[m] = mn
              self.fsm.cloud.mobiles[m].lastts = datetime.datetime.strptime(msg['ts'], '%Y-%m-%d %H:%M:%S.%f')
              logging.info(f"Mobile '{mn.name}' updated")
              found = True
          
          # new mobile
          if not found:
            self.fsm.cloud.mobiles.append(mn)
            self.fsm.cloud.mobiles[-1].lastts = datetime.datetime.strptime(msg['ts'], '%Y-%m-%d %H:%M:%S.%f')
            logging.info(f"Mobile '{mn.name}' inserted")  
                    
          # complete request
          return "CHECKCACHE", (mn,)

      elif msg['subject'] == 'EDGE' and msg['access-key'] == settings['cloud']['management']['access-key']:

        # the edge called
        logging.debug(f"Edge '{msg['body']['name']}' authorized")
        logging.debug(f"Leaving {self.name}")
        return "EDGELIST", args

      else:

        # unknown agent
        logging.debug(f"Leaving {self.name}")
        return "REFUSED", ()

    except:

      logging.error(f"Error in authorization")
      logging.debug(f"Error - Leaving {self.name}")
      return "DISCONNECT", ()

########################
class StateDISCONNECT(State):
  """The DISCONNECT state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # close the incoming connection
    self.fsm.csock.close()

    # done
    logging.debug(f"Done - Leaving {self.name}")

    # should exit from FSM
    return None, ()

#######################
class StateCONNECTED(State):
  """The CONNECTED state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    # receive something
    msg = liquidedge.receiveMessage(self.fsm.csock)
    if msg:
      # go on
      logging.debug("Received: " + str(msg))
      logging.debug(f"Presentation - Leaving {self.name}")
      return "AUTHORIZATION", (msg,)
    else:
      # go back
      logging.debug(f"Timeout - Leaving {self.name}")
      return "DISCONNECT", ()

class FiniteStateMachineConnected(FiniteStateMachine):
  """An FSM for the connected state."""

  csock: socket
  csock2: socket
  cloud: cloudorchestrator

  def __init__(self, name, sock, cd):
    # call base constructor
    super().__init__(name)
    self.csock = sock
    self.cloud = cd
    self.csock2 = None

#######################
class StateACCEPTING(State):
  """The ACCEPTING state."""

  def run(self, args):
    # begin
    logging.debug(f"Entering {self.name}")

    try:
      # accept connection
      inconn, inaddr = self.fsm.asock.accept()
      logging.debug(f"Incoming connection from {inaddr[0]}:{inaddr[1]}")

      # manage the connection with a parallel FSM
      connFsm = FiniteStateMachineConnected("CONNECTEDFSM", inconn, self.fsm.cloud)

      # add states
      connFsm.addState(StateCONNECTED())
      connFsm.addState(StateAUTHORIZATION())
      connFsm.addState(StateCHECKCACHE())
      connFsm.addState(StateGETFILES())
      connFsm.addState(StateFINDEDGE())
      connFsm.addState(StateCONTACTINGEDGE())
      connFsm.addState(StatePRESENTEDGE())
      connFsm.addState(StateEDGEFOUND())
      connFsm.addState(StateREFUSED())
      connFsm.addState(StateEDGELIST())
      connFsm.addState(StateMOBILELIST())
      connFsm.addState(StateDISCONNECT())

      # run the machine directly
      connFsm.run()

      # report
      #connFsm.summary()

      # done here
      logging.debug(f"Connection - Leaving {self.name}")
      return "ACCEPTING", ()

    except socket.timeout:
      #raise
      # nothing to do
      logging.debug(f"Timeout - Leaving {self.name}")
      return "ACCEPTING", ()

    except:
      #raise
      # done
      logging.debug(f"Shutdown - Leaving {self.name}")
      return None, ()

class FiniteStateMachineCloud(FiniteStateMachine):
  """The main FSM for the cloud."""

  asock: socket
  cloud: cloudorchestrator

  def __init__(self, name, cs):
    # call base constructor
    super().__init__(name)
    self.cloud = cs

    # creating the TCP/IP socket
    self.asock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.asock.settimeout(1) # timeout for listening
    logging.debug("Opened TCP/IP socket")

    # binding the socket and listen for receiving data up to maxconn connections
    self.asock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.asock.bind(self.cloud.address)
    self.asock.listen(settings['cloud']['management']['max-connections'])
    logging.info(f"Listening on {self.cloud.address[0]}:{self.cloud.address[1]}")

# the EDGE thread
def cleanEngine(co, cfsm):
  """Handles the cleaning engine"""
  
  logging.info(f"Cleaning engine started")

  def tooold(edgmob):
    """The edge or mobile is too old, can be deleted"""
    return (datetime.datetime.now() - edgmob.lastts).total_seconds() > 10
      
  while cfsm.active:

    logging.debug(f"Clean lists")

    # comprehension list to remove old edges
    edgelist_lock.acquire()
    co.edges[:] = [edg for edg in co.edges if not tooold(edg)]
    edgelist_lock.release()

    # comprehension list to remove old mobiles
    co.mobiles[:] = [mob for mob in co.mobiles if not tooold(mob)]

    # sleep a little
    time.sleep(1)

  logging.info(f"Cleaning engine stopped")


# the WEB thread
def webEngine(co, cfsm):
  """Handles the web engine"""
  
  logging.info(f"Web engine started")

  app = Flask(__name__, static_url_path='', static_folder='web/static', template_folder='web/templates')
  os.environ['FLASK_ENV'] = 'development'
  #app.env = 'development'

  @app.route("/")
  def index(co=None, cfsm=None):
    return render_template('index.html', mycs=mycs)

  def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
      raise RuntimeError('Not running with the Werkzeug Server')
    func()

  @app.route('/shutdown', methods=['GET'])
  def shutdown():
    shutdown_server()
    return 'Server shutting down...'

  def make_api_get_edges_and_mobiles():
    global mycs
    edgelist_lock.acquire()
    d = {'edges': mycs.to_dict()['edges'], 'mobiles': mycs.to_dict()['mobiles']}
    edgelist_lock.release()
    return d

  @app.route('/api/get/edges_and_mobiles')
  def api_get_edges_and_mobiles():
    d = make_api_get_edges_and_mobiles()
    return jsonify(d)

  app.run(host='0.0.0.0', port=settings['cloud']['management']['webport'], debug=True, use_reloader=False)

  logging.info(f"Web engine stopped")

#################
def main():
  """MAIN function."""

  # start logging
  logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='[%(levelname)s] %(asctime)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
  logging.addLevelName(logging.WARNING, "\033[1;33m%s\033[1;0m" % logging.getLevelName(logging.WARNING))
  logging.addLevelName(logging.ERROR, "\033[1;31m%s\033[1;0m" % logging.getLevelName(logging.ERROR))

  # parser
  parser = argparse.ArgumentParser(description="\033[1;1;32mLIQUID\033[1;1;33m⠪EDGE\033[1;0m cloud orchestrator agent")
  parser.add_argument("-V", '--version', help="show agent version and exit", action='version', version='%(prog)s ' + __version__)
  parser.add_argument("-c", "--config", help="cloud configuration YAML file", default="./cloud_config.yaml")
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
  local_cache_folder = os.path.join(tempfile.gettempdir(), "le_cloud_cache") if settings['cloud']['cache']['folder'] == "auto" else settings['cloud']['cache']['folder']
  if not os.path.exists(local_cache_folder):
    logging.info(f"Created local cache folder '{local_cache_folder}'")
    os.makedirs(local_cache_folder)

  # start parallel process
  if 'parallel' in settings['cloud'] and settings['cloud']['parallel']:
    try:
      subprocess.Popen(settings['cloud']['parallel'])
    except:
      logging.error(f"Could not start parallel '{settings['cloud']['parallel']}'")
      sys.exit()

  # local cloud
  global mycs
  mycs = cloudorchestrator((platform.node() + ".cloudorchestrator") if settings['cloud']['name'] == "auto" else settings['cloud']['name'])
  mycs.address = (settings['cloud']['management']['address'], settings['cloud']['management']['port'])
  mycs.cachepath = local_cache_folder
  mycs.access_key = settings['cloud']['management']['access-key']
  mycs.uuid = uuid.UUID(str(uuid.getnode()).rjust(32, '0'))
  mycs.area = settings['cloud']['area']

  # create local machine
  global cloudFsm
  cloudFsm = FiniteStateMachineCloud("NuvolaOrchestrante", mycs)

  # add states
  cloudFsm.addState(StateACCEPTING("ACCEPTING"))

  # start the web server thread
  webhandler = threading.Thread(target=webEngine, args=(mycs, cloudFsm))
  webhandler.setDaemon(True)
  webhandler.start()

  # start the edge cleaner thread
  edgehandler = threading.Thread(target=cleanEngine, args=(mycs, cloudFsm))
  edgehandler.setDaemon(True)
  edgehandler.start()

  # run the machine
  cloudFsm.run()
  #edgehandler.join()

  # report
  #cloudFsm.summary()

######################
# SCRIPT starts here #
######################
if __name__ == "__main__":
  main()
