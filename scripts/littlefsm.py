"""A small finite state machine module

You can use this to implement simple machines, such as for example
those found in simple network protocols.

Typical usage example:

  class TrafficLight(FiniteStateMachine):
  ...
  class StateGREEN(State):
  ...
  myfsm = TrafficLight("myfsm")
  myfsm.addState(StateGREEN("GREEN"))
  ...
  myfsm.run()
  myfsm.summary()
"""
from __future__ import annotations

import math
import sys
import time
from typing import Tuple

class State(object):
  """A state for a finite state machine

  This is the base class that must be derived in your application.
  The state has a method that does the most work of it and prepares
  for the transition to the next state.

  Attributes
  ----------
  fsm : FiniteStateMachine
      the reference to the finite state machine which we belong to
  name : str
      the name of the state
  ntimes : int
      the number of times in the state
  tspent : float
      the number of seconds spent in the state
  
  Methods
  -------
  run(args)
      executes the state main function
  """
  fsm: FiniteStateMachine
  name: str
  ntimes: int
  tspent: float

  def __init__(self, name: str = None):
    """
    Parameters
    ----------
    name : str
        the name of the state. If not given or None, the name is inferred from the object class name
    """
    self.fsm = None
    if name:
      self.name = name
    else:
      self.name = self.__class__.__name__[5:]
    self.ntimes = 0
    self.tspent = 0

  def run(self, args) -> Tuple[str, tuple]:
    """Executes the state main function

    This method does most of the work in the state, and returns
    the new state and arguments to it.

    Parameters
    ----------
    args : tuple
        an argument to the state main function

    Returns
    -------
    str
        the new state after the transition
    tuple
        possible arguments to the new state, or None
    """
    raise NotImplementedError()

class FiniteStateMachine(object):
  """A finite state machine with multiple states

  This is the base class that should be derived in your application.
  The machine must be populated with some states.

  Attributes
  ----------
  name : str
      the name of machine
  states : dict
      a dictionary with all the states
  prevstate : str
      the name of the state before the actual one
  active : bool
      tells whether the machine is active
  ntrans : int
      the number of transitions performed
  tstart : float
      the timestamp when the machine started
  tstop : float
      the timestamp when the machine stopped
  verbose : bool
      be more or less verbose in the output
  defaultstate : str
      the state to return at if none given
  
  Methods
  -------
  addState(st)
      add a state to the machine
  run(args)
      the main worker function of the state
  summary()
      print a resume of the machine operation
  """
  name: str
  states: dict
  prevstate: str
  active: bool
  ntrans: int
  tstart: float
  tstop: float
  verbose: bool
  defaultstate: str

  def __init__(self, name: str, vb: bool = False):
    """Create the finite state machine

    Parameters
    ----------
    name : str
        the name of the machine
    vb : bool
        Be verbose or not. Defaults to False.
    """
    self.name = name
    self.states = {}
    self.prevstate = None
    self.active = True
    self.ntrans = 0
    self.tstart = 0
    self.tstop = 0
    self.verbose = vb
    self.defaultstate = None

  def addState(self, st: State) -> int:
    """Add a state to the machine

    Parameters
    ----------
    st : State
        A state, also created right here

    Returns
    -------
    int
        the number of states in the machine so far
    """
    if st.name in self.states:
      # check if present
      print(f"WARNING: state '{st.name}' already present in '{self.name}'")
    else:
      st.fsm = self  # save a reference to the machine
      self.states[st.name] = st  # put the state in the dictionary
      if len(self.states) == 1:
        # the first added state is the default one
        self.defaultstate = st.name
    return len(self.states)

  def run(self, name: str = None):
    if self.verbose:
      print(f"FSM '{self.name}' started")
    self.tstart = time.monotonic()
    args = ()
    if not name and len(self.states) > 0:
      name = list(self.states.keys())[0]
    while self.active and name:
      prevname = name
      try:
        tspent = time.monotonic()
        name, args = self.states[name].run(args)
        tspent = time.monotonic() - tspent
        if name == "":
          name = self.defaultstate
      except KeyError:
        print(f"Error: state '{name}' not declared, exiting")
        break
      except KeyboardInterrupt:
        if self.verbose:
          print(f"FSM '{self.name}' halted")
        self.active = False
        break
      except:
        raise
      self.states[prevname].tspent += tspent
      self.states[prevname].ntimes += 1
      self.prevstate = prevname
      self.ntrans += 1
    self.tstop = time.monotonic()
    if self.verbose:
      print(f"FSM '{self.name}' ended")

  def summary(self):
    """Prints a summary of the machine, such as time spent in states and transitions done"""
    tspent = self.tstop - self.tstart
    print(f"FSM '{self.name}', {len(self.states)} states, {self.ntrans} transitions, {tspent:.6f} s:")
    n = 1
    for name, st in self.states.items():
      cpc = 100*st.ntimes/self.ntrans if self.ntrans > 0 else math.nan
      tpc = 100*st.tspent/tspent if tspent > 0 else math.nan
      print(f"    {n}. State '{name}' called {st.ntimes} times ({cpc:.1f}%) for {st.tspent:.6f} s ({tpc:.1f}%)")
      n += 1

