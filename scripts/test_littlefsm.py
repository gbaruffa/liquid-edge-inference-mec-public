#!/usr/bin/python3
import time
from littlefsm import State, FiniteStateMachine


class StateGREEN(State):
  """The green light
  """

  def run(self, args):
    self.fsm.g += 1
    for i in range(10, 0, -1):
      print(f"\r🟢⚫️⚫️: {i} s, {self.name} from {self.fsm.prevstate}, {self.fsm.g}   ", end="", flush=True)
      time.sleep(1)
    return "YELLOW", ()


class StateYELLOW(State):
  """The yellow light
  """
  
  def run(self, args):
    self.fsm.o += 1
    tmo = args[0] if args else 4
    for i in range(tmo, 0, -1):
      print(f"\r⚫️🟡⚫️: {i} s, {self.name} from {self.fsm.prevstate}, {self.fsm.o}   ", end="", flush=True)
      time.sleep(1)
    return "RED", ()


class StateRED(State):
  """The red light
  """
  
  def run(self, args):
    self.fsm.r += 1
    for i in range(15, 0, -1):
      epoch = (time.monotonic() - self.fsm.t0) % 120
      if epoch < 60:
        print(f"\r⚫️⚫️🔴: {i} s, {self.name} from {self.fsm.prevstate}, {self.fsm.r}   ", end="", flush=True)
        time.sleep(1)
      else:
        return "FLASHINGYELLOW", ()
    return "GREEN", ()


class StateFLASHINGYELLOW(State):
  """The flashing yellow light
  """

  def run(self, args):
    self.fsm.f += 1
    for i in range(60):
      if i % 2 < 1:
        print(f"\r⚫️🟡⚫️: {i} s, {self.name} from {self.fsm.prevstate}, {self.fsm.f}   ", end="", flush=True)
      else:
        print(f"\r⚫️⚫️⚫️​: {i} s, {self.name} from {self.fsm.prevstate}, {self.fsm.f}   ", end="", flush=True)
      time.sleep(1)
    return "YELLOW", (10,)


class TrafficLight(FiniteStateMachine):
  """A traffic light
  """

  t0: float
  g: int
  o: int
  r: int
  f: int
  
  def __init__(self, name):
    super().__init__(name)
    self.t0 = time.monotonic()
    self.g = self.o = self.r = self.f = 0

myfsm = TrafficLight("myfsm")
myfsm.addState(StateGREEN("GREEN"))
myfsm.addState(StateYELLOW("YELLOW"))
myfsm.addState(StateFLASHINGYELLOW("FLASHINGYELLOW"))
myfsm.addState(StateRED("RED"))
myfsm.run()
myfsm.summary()

myfsm2 = TrafficLight("myfsm2")
myfsm2.summary()