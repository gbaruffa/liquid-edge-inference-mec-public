# References
# 1. https://github.com/nathanrhart/Python-TCP-Server/blob/master/tcp_server.py
# 2. https://gist.github.com/Integralist/3f004c3594bbf8431c15ed6db15809ae
# 3. https://stackoverflow.com/questions/13979764/python-converting-sock-recv-to-string
# 4. https://stackoverflow.com/questions/64386571/python-tcp-socket-send-string
# 5. https://www.freecodecamp.org/news/how-to-substring-a-string-in-python/
# 6. https://stackoverflow.com/questions/275018/how-can-i-remove-a-trailing-newline
# 7. https://stackoverflow.com/questions/1131220/get-md5-hash-of-big-files-in-python
# 8. https://stackoverflow.com/questions/17667903/python-socket-receive-large-amount-of-data
# 9. https://www.pythonpool.com/python-delete-file/
# 10. https://docs.python.org/3/library/stdtypes.html?highlight=strip#str.strip
# 11. https://www.tutorialspoint.com/python-generate-random-string-of-given-length
# 12. https://docs.python.org/3/library/os.path.html
# 13. https://linuxize.com/post/python-check-if-file-exists/
# 14. https://www.csestack.org/get-file-size-python/
# 15. https://stackoverflow.com/questions/384076/how-can-i-color-python-logging-output

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import socket
import uuid
import time
import subprocess
from littlefsm import State, FiniteStateMachine

# https://subscription.packtpub.com/book/networking-and-servers/9781786463999/1/ch01lvl1sec15/writing-a-simple-tcp-echo-client-server-application
def keepalive_connection(pp: list[subprocess.Popen[bytes]] = None, addrm: str = None, portm: int = None, incoming: bool = False, fsm: FiniteStateMachine = None):
  """Open and monitor a TCP connection"""

  if not incoming:

    # client side (mobile)
    logging.info(f"Opening keepalive connection to {addrm}:{portm}")

    # Create a TCP/IP socket 
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
    
    # Connect the socket to the server 
    logging.debug(f"Connecting to {addrm} port {portm}") 
    sock.connect((addrm, portm)) 
     
    # Send data perpetually
    while fsm.active:

      # Send data 
      try: 
        message = "KEEPALIVE MESSAGE" 
        #print("Sending %s" % message) 
        sock.sendall(message.encode('utf-8')) 

        # Look for the response 
        sock.settimeout(1.0)
        data = sock.recv(2048) 
        sock.settimeout(None)
        #print("Received: %s" % data) 
        print("KEEPALIVE", end='\r', flush=True)

      except socket.error as e: 
          logging.error(f"Socket error: {e}") 
          break

      except Exception as e: 
          logging.error(f"Other exception: {e}") 
          break

      # finally: 
      #     print("Closing connection to the server") 
      #     sock.close() 

      time.sleep(0.5) # 2 msgs per second

  else:

    # server side (edge)
    logging.info(f"Expecting keepalive connection on {addrm}:{portm}")

    # Create a TCP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Enable reuse address/port
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Bind the socket to the port
    logging.debug(f"Starting up server on {addrm}:{portm}")
    sock.bind((addrm, portm))
    sock.settimeout(3)

    # Listen to clients, backlog argument specifies the max no. of queued connections
    logging.debug("Waiting to receive message from client")
    sock.listen(0)
    try:

      client, address = sock.accept()
      stop = False
      while not stop and fsm.active:

        # receive periodic ping
        client.settimeout(1.0)
        data = client.recv(2048)
        client.settimeout(None)

        # verify and respond to ping
        if data:
          client.send(data)
        else:
          break

        # check that the main subprograms are still running
        if pp:
          for p in pp:
            if p.poll() is None:
              #print(p.args)
              pass
            else:
              stop = True
              break

        # all good
        print("KEEPALIVE", end='\r', flush=True)

    except socket.timeout:

      pass

  logging.warning("Keepalive connection closed")
  
def generateFileMD5(filename: str, blocksize: int = 2**20) -> str:
  """Generates the MD5 hash of a file

  Parameters
  ----------
  filename : str
      Path of the file whose MD5 hash will be computed
  blocksize : int, optional
      Buffer size to read large files in small chunks (default is 2^20)

  Returns
  -------
  str
      Lowercase hexadecimal string representing the MD5 hash
  """
  # init MD5
  m = hashlib.md5()

  # open file
  with open(filename, "rb") as f:
    while True:

      # read in small chunks
      buf = f.read(blocksize)
      if not buf:
        # file ended
        break

      # update hash
      m.update(buf)
      
  # convert hash into hexadecimal string
  return m.hexdigest()

class dnnfile(object):
  """A deep neural network file"""
  path: str
  name: str
  size: int
  hash: str
  typ: str
  cached: bool
  
  def __init__(self, typ: str, name: str = None, path: str = None, size: int = None, hash: str = None):
    """Construct a DNN file
    
    Parameters
    ----------
    typ : str
        defines the file type
    name : str
        the file name
    path : str
        full or relative path to file
    size : int
        byte size of the file
    hash : str
        MD5 hash digest as a string
    """
    self.path = path
    self.name = name
    self.size = size
    self.hash = hash
    self.type = typ
    self.cached = False

  def to_dict(self) -> dict:
    """Serialize to a dictionary
    
    Returns
    -------
    dict
        the object as a dict
    """
    return {'type': self.type, 'name': self.name, 'path': self.path, 'size': self.size, 'hash': self.hash}

  @staticmethod
  def from_dict(d: dict) -> dnnfile:
    """Deserialize from a dictionary
    
    Parameters
    ----------
    d : dict
        the object as a dictionary
    
    Returns
    -------
    dnnfile
        the deserialized object
    """
    try:
      return dnnfile(d['type'], d['name'], None, d['size'], d['hash'])
    except:
      # there was some error
      return None

class engine(object):
  name: str
  version: str
  path: str
  keepalive: bool
  viewport: int
  def __init__(self, name: str = "", version: str = "", path: str = "", keepalive: bool = False, viewport: int = None):
    self.name = name
    self.version = version
    self.path = path
    self.keepalive = keepalive
    self.viewport = viewport
  def to_dict(self) -> dict:
    return {'name': self.name, 'version': self.version, 'path': self.path, 'keepalive': self.keepalive, 'viewport': self.viewport}
  @staticmethod
  def from_dict(d: dict) -> engine:
    try:
      e = engine(d['name'], d['version'], d['path'], d['keepalive'], d['viewport'])
      return e
    except:
      return None

class mobilenode(object):
  name: str
  address: tuple
  coordinates: tuple
  serialport: str
  uuid: uuid
  ranaddresses: list[str]
  files: list[dnnfile]
  engine: engine
  hardware: hwm
  ranprop: dict
  lastts: datetime
  def __init__(self, name: str):
    self.name = name
    self.address = ()
    self.coordinates = ()
    self.serialport = ""
    self.uuid = None
    self.ranaddresses = None
    self.files = None
    self.engine = None
    self.hardware = None
    self.ranprop = {}
    self.lastts = datetime.datetime(1970, 1, 1, 0, 0, 0)
  def to_dict(self) -> dict:
    d = {
      'name': self.name,
      'address': self.address,
      'coordinates': self.coordinates,
      'serialport': self.serialport,
      'uuid': str(self.uuid),
      'ranaddresses': [],
      'files': [],
      'engine': self.engine.to_dict(),
      'hardware': self.hardware.to_dict(),
      'ranprop': self.ranprop,
      'lastts': self.lastts.strftime('%Y-%m-%d %H:%M:%S.%f'),
    }
    # for r in self.ranaddresses:
    #   d['ranaddresses'].append(r)
    for f in self.files:
      d['files'].append(f.to_dict())
    return d
  @staticmethod
  def from_dict(d: dict) -> mobilenode:
    try:
      mn = mobilenode("")
      mn.name = d['name']
      mn.address = d['address']
      mn.coordinates = d['coordinates']
      mn.serialport = d['serialport']
      mn.uuid = uuid.UUID(d['uuid'])
      try:
        mn.lastts = datetime.datetime.strptime(d['lastts'], '%Y-%m-%d %H:%M:%S.%f')
      except:
        pass
      mn.files = []
      for o in d['files']:
        mn.files.append(dnnfile.from_dict(o))
      mn.engine = engine.from_dict(d['engine'])
      mn.hardware = hwm.from_dict(d['hardware'])
      mn.ranprop = d['ranprop']
      return mn
    except:
      return None

class pu(object):
  def __init__(self, model: str = "", arch: str = "", load: float = 0.0):
    self.model = model
    self.arch = arch
    self.load = load
  def to_dict(self) -> dict:
    return {'model': self.model, 'arch': self.arch, 'load': self.load}
  @staticmethod
  def from_dict(d: dict) -> pu:
    try:
      return pu(d['model'], d['arch'])
    except:
      return None

class hw(object):
  """Describes the hardware capabilities of a device
  """
  cpus: list
  gpus: list
  def __init__(self):
    self.cpus = []
    self.gpus = []
  def to_dict(self) -> dict:
    d = {'cpus': [], 'gpus': []}
    for c in self.cpus:
      d['cpus'].append(c.to_dict())
    for g in self.gpus:
      d['gpus'].append(g.to_dict())
    return d
  @staticmethod
  def from_dict(d: dict) -> hw:
    return None

class hwm(hw):
  robot_model: str
  def __init__(self, rm: str = ""):
    super().__init__()
    self.robot_model = rm
  def to_dict(self) -> dict:
    d = super().to_dict()
    d['robot-model'] = self.robot_model
    return d
  @staticmethod
  def from_dict(d: dict) -> hwm:
    try:
      h = hwm(d['robot-model'])
      for sc in d['cpus']:
        h.cpus.append(pu.from_dict(sc))
      for sg in d['gpus']:
        h.gpus.append(pu.from_dict(sg))
      return h
    except:
      return None

class edgecomputer(object):
  address: tuple
  cachepath: str
  cachesize: int
  access_key: str
  avail: float
  uuid: uuid
  name: str
  lastts: datetime
  hardware: hw
  engines: list
  ranprop: dict
  mobiles: list
  def __init__(self, name: str):
    self.address = ()
    self.cachepath = None
    self.cachesize = 0
    self.access_key = None
    self.avail = 1.0
    self.uuid = None
    self.name = name
    self.lastts = datetime.datetime(1970, 1, 1, 0, 0, 0)
    self.hardware = hw()
    self.engines = []
    self.ranprop = {}
    self.mobiles = []
  def to_dict(self) -> dict:
    d = {
      'address': self.address,
      'access-key': self.access_key,
      'availability': self.avail,
      'uuid': str(self.uuid),
      'name': self.name,
      'lastts': self.lastts.strftime('%Y-%m-%d %H:%M:%S.%f'),
      'hardware': self.hardware.to_dict(),
      'engines': [],
      'ranprop': self.ranprop,
      'mobiles': [],
      'cachesize': self.cachesize
    }
    for e in self.engines:
      d['engines'].append(e.to_dict())
    for m in self.mobiles:
      d['mobiles'].append(m.to_dict())
    return d
  @staticmethod
  def from_dict(d: dict) -> edgecomputer:
    try:
      ec = edgecomputer("")
      ec.address = (d['address'][0], d['address'][1])
      ec.access_key = d['access-key']
      ec.avail = d['availability']
      ec.uuid = uuid.UUID(d['uuid'])
      ec.name = d['name']
      try:
        ec.lastts = datetime.datetime.strptime(d['lastts'], '%Y-%m-%d %H:%M:%S.%f')
      except:
        pass
      for sc in d['hardware']['cpus']:
        ec.hardware.cpus.append(pu(sc['model'], sc['arch'], sc['load']))
      for sg in d['hardware']['gpus']:
        ec.hardware.gpus.append(pu(sg['model'], sg['arch'], sg['load']))
      for se in d['engines']:
        ec.engines.append(engine(se['name'], se['version'], se['path'], se['keepalive'], se['viewport']))
      ec.ranprop = d['ranprop']
      for sm in d['mobiles']:
        ec.mobiles.append(mobilenode.from_dict(sm))
      ec.cachesize = d['cachesize']
      return ec
    except:
      return None

class cloudorchestrator(object):

  address: tuple
  edges: list
  mobiles: list
  cachepath: str
  name: str
  uuid: uuid
  access_key: str
  area: dict

  def __init__(self, name: str = ""):
    self.address = ()
    self.edges = []
    self.mobiles = []
    self.cachepath = None
    self.name = name
    self.uuid = None
    self.access_key = None
    self.area = {}

  def to_dict(self) -> dict:
    d = {
      'address': self.address,
      'edges': [],
      'mobiles': [],
      'name': self.name,
      'uuid': str(self.uuid),
      'access_key': self.access_key,
      'area': self.area
    }
    for e in self.edges:
      d['edges'].append(e.to_dict())
    for m in self.mobiles:
      d['mobiles'].append(m.to_dict())
    return d

  def matchEdgeComputer0(self, mn: dict):
    """Match an edge computer to the mobile node - LEGACY"""
    # search the edge
    for ei in range(len(self.edges)):
      logging.debug(f"Edge #{ei + 1}: {self.edges[ei].to_dict()}")
      if self.edges[ei].avail > 0.75:  # must be available
        for n in self.edges[ei].engines:
          if n.name == mn.engine.name: # must have the requested engine
            return self.edges[ei]
    return {}

  def matchEdgeComputer(self, mn):
    """Match an edge computer to the mobile node, if possible.
    
    This function searches through the list of registered edge computers
    and finds the first edge whose RAN AP MAC address matches one entry in the mobile node
    list of remote RAN AP MAC addresses. It then further checks if the edge computer has
    enough CPU to share, and if it has the proper engine installed.
    """
    # search the edges
    for ec in self.edges:
      logging.debug(f"Edge: {ec.to_dict()}")
      # search the mobile list of connected APs
      for rp in mn.ranprop:
        if ec.ranprop['mac-address'] == rp['remote-address']: # the RAN AP MAC addresses must coincide
          if ec.avail > 0.75:  # must have enough CPU power
            for ng in ec.engines:
              if ng.name == mn.engine.name: # must have the requested engine
                return ec
    return {}

# send a message over the socket
def sendMessage(conn: socket, msg: dict, timeout: float = 2) -> bool:
  try:
    messagestr = json.dumps(msg) + "\n"
  except:
    return False
  try:
    conn.settimeout(timeout)
    conn.sendall(messagestr.encode())
    return True
  except socket.error:
    return False

# receive a message over the socket
def receiveMessage(conn: socket, timeout: float = 2) -> dict:
  data = ""
  conn.settimeout(timeout)
  while True:
    try:
      datum = conn.recv(1).decode('utf-8')
      if not datum or datum == '\n':
        break
      else:
        data += datum
    except:
      break
  try:
    return json.loads(data)
  except:
    return None

def isNaN(num):
  return num!= num