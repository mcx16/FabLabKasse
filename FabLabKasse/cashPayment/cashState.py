#!/usr/bin/env python2.7
# -*- coding: utf-8 -*-
#
# FabLabKasse, a Point-of-Sale Software for FabLabs and other public and
# trust-based workshops.
# Copyright (C) 2014  Julian Hammer <julian.hammer@fablab.fau.de>
#                     Maximilian Gaukler <max@fablab.fau.de>
#                     Timo Voigt <timo@fablab.fau.de>
#
# This program is free software: you can redistribute it and/or modify it under the terms of the GNU
# General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
# even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this program. If not,
# see <http://www.gnu.org/licenses/>.

"""Cash storage (coins and banknotes) tracking

Usage:
  cash show
  cash log [<fromDate> <untilDate>]
  cash set [--force-new] <device> <state> <comment>...
  cash check <device> <state> [<other options ignored>...]
  cash add <device> <stateDelta> <comment>...
  cash move <fromDevice> <toDevice> <stateDelta> <comment>...
  cash verify
  cash help

Options:
  --force-new   Allow using set,add,move with devices that have no database entry yet
  -h --help     Show this screen.
  --version     Show version.

Explanation:
set: set new state, ignoring old state (after manually counting)
check: compare current state to the one given on the commandline. Show difference if it does not match.
add: increment state (after manually inserting a coin)
move: transfer coins (internally, not to customer) from one storage to another
verify: check that cash-sum matches kassenbuch

device format is: identifier.subindex
state and stateDelta format is: /13x10c,53x20E/ for 13 * 10 cent and 53 * 20 Euro
// represents an empty cash state (0 cash).

"""

import json
from datetime import datetime, timedelta
import dateutil.parser
from .. import scriptHelper
from ..kassenbuch import Kasse
from decimal import Decimal
import copy
import sys

from termcolor import colored


def coloredError(s):
    return colored(s, 'red',  attrs=['bold'])


def coloredGood(s):
    return colored(s, 'green',  attrs=['bold'])


def coloredBold(s):
    return colored(s, 'blue',  attrs=['bold'])


class NoDataFound(Exception):
    pass


class CashState(object):

    """
    cash storage state for one particular storage (e.g. a cashbox) containing coins and banknotes
    e.g. "13 * 1€ and 25 * 0,02€"
    """

    def __init__(self, dictionary=None):
        # a default value dictionary={} would be a dangerous piece of code, google PyLint W0102 for more infos.
        if dictionary == None:
            dictionary = {}
        for key in dictionary.keys():
            assert type(key) == int
            assert key in [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000,
                           5000, 10000, 20000, 50000], "invalid denomination"
        for value in dictionary.values():
            assert type(value) == int

        # remove useless zero-entries like "0x10€"
        def filter_out_zero_values(dictionary):
            output = {}
            for key in dictionary:
                if dictionary[key] == 0:
                    continue
                output[key] = dictionary[key]
            return output

        self._d = filter_out_zero_values(dictionary)

    def __add__(self, other):
        assert type(self) == type(other)
        # extract dicts
        sumState = copy.deepcopy(self._d)
        stateDelta = other.toDict()

        # add up
        for denomination in stateDelta.keys():
            if denomination not in sumState:
                sumState[denomination] = 0
            sumState[denomination] += stateDelta[denomination]

        # convert dict to CashState
        sumState = CashState(sumState)

        # sanity check
        assert self.sum + other.sum == sumState.sum
        return sumState

    def __sub__(self, other):
        assert type(other) == type(self)
        # basic check: negating twice should be identity
        assert -(-other) == other  # pylint: disable=E0107
        return self + (-other)

    def __neg__(self):
        dataNegative = {}
        for (key, value) in self._d.items():
            dataNegative[key] = -value
        negative = CashState(dataNegative)
        assert self.sum == -(negative.sum)
        return negative

    @property
    def sum(self):
        return sum([key * value for (key, value) in self._d.iteritems()])

    def sumStr(self):
        return "{:.2f}".format(self.sum / 100.)

    def toDict(self):
        """
        return dictionary {denomination: value, ...}
        """
        return copy.deepcopy(self._d)

    def toJSON(self):
        """
        convert state (dict of denomination : amount) {100: 3, 200: 5} to JSON encoded string
        both denomination and amount must be integer
        """
        s = json.dumps(self._d)
        assert self == CashState.fromJSON(s), \
            "decoding did not return equal state"
        return s

    @classmethod
    def fromJSON(cls, s):
        """ load from CashState.toJSON """
        state = json.loads(s)
        convertedState = {}
        assert type(state) == dict, "error decoding state"
        for (key, value) in state.items():
            assert type(value) == int
            convertedState[int(key)] = value
        return cls(convertedState)

    def __eq__(self, other):
        if type(self) != type(other):
            return False
        return self._d == other.toDict()

    def toHumanString(self):
        s = "/"
        for (key, val) in sorted(self._d.iteritems()):
            if key >= 100 and key % 100 == 0:
                s += "{}x{}E,".format(val, key / 100)
            else:
                s += "{}x{}c,".format(val, key)
        # remove trailing comma, add  slash
        if s[-1] == ",":
            s = s[:-1]
        s += "/"
        assert self == CashState.fromHumanString(s),\
            "decoding didnt return equal state"
        return s

    def toVerboseString(self):
        return "{}\t{}".format(self.sumStr(), self.toHumanString())

    @classmethod
    def fromHumanString(cls, s):
        """ state from string with a more human-friendly format: /13x10c,53x200E/ """
        state = {}
        s = s.strip()
        assert (s[0] == "/" and s[-1] == "/"), \
            "state string must be enclosed in /.../, " \
            " if you want wo set to zero use //"
        s = s[1:-1]  # discard { and }
        if s == "":
            return cls()
        for t in s.split(","):
            tempList = t.split("x")
            assert len(tempList) == 2, \
                "state format must be /13x10c,53x200E/ (13 * 10 cent, 53 * 200 euro)"
            [val, key] = tempList
            assert key[-1] in ["c", "E"],  "key must end with c for cents or E for euro. example: 13x10c"
            keyInt = int(key[0:-1])
            if key[-1] == "E":
                keyInt = keyInt * 100
            key = keyInt
            val = int(val)
            assert key not in state.keys(), \
                "state indices must be unique, NOT e.g. {12*100€, 13*100€}"
            state[key] = val
        return cls(state)


class CashStorage(object):

    """
    cash storage (coins, banknotes) for vending machines

    hierarchy:
    identifier (unique device name)
    -> subindex (unique index for separate cash storages, e.g. cashbox, tube1, etc. in a coin dispenser)
    -> {denomination: count,  ...}  (type and count of coins/banknotes. denomination is an integer value (Euro cents).
    """

    # TODO does not enforce uniqueness among separate processes :(
    __usedIdentifiers = []

    def __init__(self, db, identifier, readonly=True):
        """
        db: sqlite database
        identifier: unique device name
        readonly: forbid write access
        """
        self.db = db
        self.db.execute("CREATE TABLE IF NOT EXISTS cash(id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL, date TEXT NOT NULL, state TEXT NOT NULL, updateType TEXT NOT NULL, isManual INTEGER NOT NULL, comment TEXT)")
        self.identifier = identifier
        self.readonly = readonly
        if not readonly:
            # the same identifier may not be used by two instances
            # (they would overwrite each other's data)
            assert not "." in self.identifier
            if identifier in CashStorage.__usedIdentifiers:
                raise ValueError("CashState identifier already in use for writing in this process")
            CashStorage.__usedIdentifiers.append(identifier)

    # allowEmpty: True -> quietly continue - return CashState() when device or subindex does not exist in DB
    # allowEmpty: False -> raise NoDataFound()  when device or subindex does not exist.
    # default is True, because otherwise it would mess up starting with empty DB
    def getState(self, subindex="main", allowEmpty=True):
        dev = self.identifier + "." + subindex
        cur = self.db.cursor()
        cur.execute("SELECT state FROM cash WHERE device = ? ORDER BY id DESC LIMIT 1 ", (dev, ))
        row = cur.fetchone()

        if row is None:
            if not allowEmpty:
                raise NoDataFound()
            return CashState()
        else:
            return CashState.fromJSON(row[0])

    def getStateVerbose(self, subindex):
        state = self.getState(subindex)
        return state.toVerboseString()

    def _storeState(self, subindex, state, updateType, isManual, comment):
        assert not self.readonly
        device = self.identifier + "." + subindex
        date = datetime.now()  # .strftime( '%Y-%m-%d %H:%M:%S.%f')
        assert type(state) == CashState
        assert type(isManual) == bool
        assert updateType in ["add", "set", "log", "move"]
        assert (updateType == "log" and subindex == "log") or (updateType != "log" and subindex != "log"), \
            "For logging, you must use updateType=log and subindex=log. " \
            "All other operations must not use 'log' as updateType or subindex."
        self.db.execute("INSERT INTO cash (device, date, state, updateType, isManual, comment) VALUES (?, ?, ?, ?, ?, ?)", (device, date, state.toJSON(), updateType, isManual,  comment))

    def setState(self, subindex, state, isManual=False, comment=""):
        """ set the new absolute state to the given value.
            use this only if you have completely certain values (from your device or from manually counting)
        """
        assert type(state) == CashState
        self._storeState(subindex=subindex, state=state, updateType="set",
                         isManual=isManual, comment=comment)
        self.db.commit()

    def addToState(self, subindex, stateDelta, isManual=False, comment="", _isLogMessage=False):
        """
        increment the current state by stateDelta=CashState({denomination:count,...})
        e.g. when a coin was accepted or paid out

        _isLogMessage: internal, only to be used by log()
        """
        assert type(stateDelta) == CashState
        # read - calculate - update
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")  # acquite write-lock *before* reading
            state = self.getState(subindex)  # get current state
            # calculate new state, uses CashState.__add__()
            newState = state + stateDelta
            # additional sanity check that cannot be inside CashState.__add__()
            assert state + stateDelta == stateDelta + state

            # store new state
            updateType = "add"
            if _isLogMessage:
                # logging is like adding a zero state
                assert stateDelta == CashState()
                assert subindex == "log"
                updateType = "log"
            self._storeState(subindex=subindex, state=newState, updateType=updateType, isManual=isManual, comment=comment)
            # implicit commit/rollback because of "with" block

    def moveToOtherSubindex(self, fromSubindex, toSubindex, denomination, count, comment="", isManual=False):
        """ move coins/banknotes (denomination * count) from one subindex to another (e.g. banknote recycler to banknote cashbox) """
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")  # acquite write-lock *before* reading
            # read, calculate, then update
            newFromState = self.getState(fromSubindex) + CashState({denomination: -count})
            newToState = self.getState(toSubindex) + CashState({denomination: +count})

            self._storeState(subindex=fromSubindex, state=newFromState, updateType="move", isManual=isManual, comment=comment)
            self._storeState(subindex=toSubindex, state=newToState, updateType="move", isManual=isManual, comment=comment)

            # implicit commit/rollback because of "with" block

    def log(self, comment, isManual=False):
        """save a string to the database, used for logging of important events"""
        self.addToState(subindex="log", stateDelta=CashState(), isManual=isManual,  comment=comment, _isLogMessage=True)


class CashStorageList(object):

    """
    A container of multiple CashStorage devices, representing a whole vending machine
    Used for retrieving the state of all devices at once
    """

    def __init__(self, db):
        self.db = db

    @property
    def states(self):
        cur = self.db.cursor()
        cur.execute("SELECT device FROM cash GROUP BY device;")
        state = {}
        for row in cur.fetchall():
            dev = row[0]
            [name, subindex] = dev.split(".")
            state[dev] = CashStorage(self.db, name).getState(subindex)
        return state

    def statesStr(self):
        s = "States per subindex:\n"
        totalState = CashState()

        for (dev, state) in sorted(self.states.iteritems()):
            if dev.endswith(".log"):
                # skip empty "log" subindex used for logging
                assert state == CashState()
                continue
            s += "{}\t{}\n".format(dev, state.toVerboseString())
            totalState += state

        s += "\n===========\nsum per device:\n"
        # sum over all subindices of one device
        currentDev = None
        perDeviceSum = None
        for (dev, state) in sorted(self.states.iteritems()) + [(".", CashState())]:
            [device, _] = dev.split(".")
            if device != currentDev:
                if currentDev != None:
                    s += "{}\t{}\n".format(currentDev, perDeviceSum.toVerboseString())
                currentDev = device
                perDeviceSum = CashState()
            perDeviceSum += state
        s += "\n===========\n"
        s += coloredBold("TOTAL") + ":\t{}".format(totalState.toVerboseString())
        return s

    # How much money is inside the whole vending machine?
    @property
    def total(self):
        return sum([state.sum for state in self.states.values()]) * Decimal('0.01')


def printVerify(db):
    cfg = scriptHelper.getConfig()
    k = Kasse(cfg.get('general', 'db_file'))
    summeKassenbuch = 0
    for buchung in k.get_buchungen():
        if buchung.konto != "Automatenkasse":
            continue
        summeKassenbuch += buchung.betrag
    summeKasse = CashStorageList(db).total
    if summeKasse == summeKassenbuch:
        print coloredGood("Abgleich OK") + ": Kasse=Kassenbuch={}".format(summeKasse)
    else:
        print coloredError("Achtung, Abweichung:") + " Kasse: {}, Kassenbuch: {}, Zuviel in Kasse: {}".format(summeKasse, summeKassenbuch, summeKasse - summeKassenbuch)




def printLog(db, date_from=None, date_to=None):
    """print all cash movements in the given time region

    date_from, date_to: ISO date strings  like "2014-02-25", or None
    """
    cur = db.cursor()
    cur.execute('SELECT device, date, state, updateType, isManual, comment FROM cash')

    def dateFromString(s, default=None):
        if not s:
            s = default
        return dateutil.parser.parse(s)
    fromDate = dateFromString(date_from, "1900-01-01")
    untilDate = dateFromString(date_to, "3456-01-01") + timedelta(days=1, microseconds=-1)
    if date_from:
        print "showing from {}".format(fromDate)
    columnTitles = ["device.sub", "date", "manual?", "type", "comment", "delta", "delta", "dev.total", "dev.total"]
    columnWidths = [15, 26, 7, 4, 40, 8, 20, 8, 20]

    def printFormatted(output):
        for i in range(len(output)):
            output[i] = unicode(output[i])
            l = len(output[i])
            output[i] = output[i].ljust(columnWidths[i])
            if l > columnWidths[i]:
                output[i] = output[i][0:columnWidths[i] - 3] + "..."
        print u"|".join(output)
    printFormatted(columnTitles)
    previousStates = {}
    for row in cur:
        output = [row[0], row[1]]
        dev = row[0]
        # state: compute delta, because the DB only stores the total
        currentState = CashState.fromJSON(row[2])
        if previousStates.has_key(dev):
            delta = currentState - previousStates[dev]
            delta = delta.toVerboseString()
        else:
            delta = "<start>\t<start>"
        previousStates[dev] = currentState
        if not (fromDate <= dateFromString(row[1]) <= untilDate):
            continue  # row not in filtered date region
        # manual?
        if int(row[4]) == 0:
            output.append("")
        else:
            output.append("MANUAL")
        output.append(row[3])  # type
        output.append(row[5])  # comment

        # delta
        output += delta.split("\t")
        output += currentState.toVerboseString().split("\t")

        printFormatted(output)
    if date_to:
        print "showing until {}".format(untilDate)


def checkIfDeviceExists(db, identifier, subindex):
    ''' check if given cash device has entries in the database. if not, exit with error message. '''
    try:
        # check if given name exists
        cash = CashStorage(db, identifier, readonly=True)
        cash.getState(subindex, allowEmpty=False)
    except NoDataFound:
        print "Error: Given device or subindex  '{}.{}' does not exist in database.".format(identifier, subindex)
        print "If this is not a typo and the device was never used yet, please use --force-new"
        sys.exit(1)


def splitDeviceName(dev):
    assert "." in dev, \
        "devicename must be <identifier>.<subindex>"
    [identifier, subindex] = dev.split(".")
    return [identifier, subindex]


def main():
    from docopt import docopt
    arguments = docopt(__doc__, version='cashState.py')

    # Python2.7 fixup: decode UTF8 arguments
    def decodeUtf8(x):
        if type(x) == str:
            return x.decode("utf-8")
        elif type(x) == list:
            return map(decodeUtf8, x)
        else:
            return x

    for key in arguments.iterkeys():
        arguments[key] = decodeUtf8(arguments[key])

    db = scriptHelper.getDB()
    CashStorage(db, "dummy")  # call constructor to create database if it doesnt exist

    # common argument preprocessing
    if arguments['<device>']:
        [identifier, subindex] = splitDeviceName(arguments['<device>'])
        if subindex == "log":
            print "Writing to the .log subindex is not supported. For adding comments, please add an empty state on another subindex."
            sys.exit(1)
    if arguments['<fromDevice>']:
        [identifierFrom, subindexFrom] = splitDeviceName(arguments['<fromDevice>'])
    if arguments['<toDevice>']:
        [identifierTo, subindexTo] = splitDeviceName(arguments['<toDevice>'])
    if arguments['<comment>']:
        comment = u" ".join(arguments['<comment>'])

    if arguments['help']:
        print __doc__
    elif arguments['show']:
        print CashStorageList(db).statesStr()
        printVerify(db)
    elif arguments['set'] or arguments['add'] or arguments['check']:
        cash = CashStorage(db, identifier, readonly=arguments['check'])
        if (arguments['set'] or arguments['add']) and not arguments['--force-new']:
            checkIfDeviceExists(db, identifier, subindex)
        # now do the actual work:
        if arguments['set']:
            state = CashState.fromHumanString(arguments['<state>'])
            cash.setState(subindex, state, isManual=True, comment=comment)
        elif arguments['add']:
            stateDelta = CashState.fromHumanString(arguments['<stateDelta>'])
            cash.addToState(subindex, stateDelta, isManual=True, comment=comment)
        elif arguments['check']:
            newState = CashState.fromHumanString(arguments['<state>'])
            try:
                oldState = cash.getState(subindex, allowEmpty=False)
                if newState == oldState:
                    print "Okay, state matches."
                else:
                    print "States do not match!"
                    print "Difference new-current is " + (newState - oldState).toVerboseString()
                    print "current state: " + oldState.toVerboseString()
                    print "new state:     " + newState.toVerboseString()
            except NoDataFound:
                print "Error: Given device or subindex does not (yet?) exist in database."
                sys.exit(1)
        if not arguments['check']:
            print "new state is now: " + cash.getStateVerbose(subindex)
            printVerify(db)
    elif arguments['log']:
        printLog(db, arguments['<fromDate>'], arguments['<untilDate>'])
    elif arguments['move']:
        assert identifierFrom == identifierTo,  "moving between two different devices is not supported, please use two 'add' operations."
        stateDelta = CashState.fromHumanString(arguments['<stateDelta>'])
        stateDeltaDict = stateDelta.toDict()
        assert len(stateDeltaDict) == 1,  "move supports only exactly one coin type at once"
        denomination = stateDeltaDict.keys()[0]
        count = stateDeltaDict.values()[0]
        if not arguments['--force-new']:
            checkIfDeviceExists(db, identifierFrom, subindexFrom)
            checkIfDeviceExists(db, identifierTo, subindexTo)
        cash = CashStorage(db, identifierFrom, readonly=False)
        cash.moveToOtherSubindex(subindexFrom, subindexTo, denomination, count, comment, isManual=True)
    elif arguments['verify']:
        printVerify(db)
    else:
        print "option not implemented"
        print arguments
        # if arguments['device']

if __name__ == '__main__':
    main()
