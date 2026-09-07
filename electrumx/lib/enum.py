# Copyright (c) 2016, Neil Booth
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''An enum-like type with reverse lookup.

Source: Python Cookbook, http://code.activestate.com/recipes/67107/
'''


class EnumError(Exception):
    pass


class Enumeration:

    def __init__(self, name, enumList):
        self.__doc__ = name

        lookup = {}
        reverseLookup = {}
        i = 0
        uniqueNames = set()
        uniqueValues = set()
        for x in enumList:
            if isinstance(x, tuple):
                x, i = x
            if not isinstance(x, str):
                raise EnumError("enum name {} not a string".format(x))
            if not isinstance(i, int):
                raise EnumError("enum value {} not an integer".format(i))
            if x in uniqueNames:
                raise EnumError("enum name {} not unique".format(x))
            if i in uniqueValues:
                raise EnumError("enum value {} not unique".format(x))
            uniqueNames.add(x)
            uniqueValues.add(i)
            lookup[x] = i
            reverseLookup[i] = x
            i = i + 1
        self.lookup = lookup
        self.reverseLookup = reverseLookup
        # Bind every member as a real instance attribute.
        #
        # Without this, an access like OpCodes.OP_DUP misses normal attribute lookup and falls
        # through to __getattr__ below -- a Python-level call plus a dict get. The script parsers
        # do roughly three of those per opcode inside their inner loops, which profiling measured
        # at a third of base_locking_script's runtime on a real 238-byte dMint contract script.
        # Bound here, the same access is a C-level instance-dict hit and __getattr__ is never
        # reached. It stays as the fallback so an unknown member still raises AttributeError.
        for name, value in lookup.items():
            if name in self.__dict__:
                raise EnumError("enum name {} shadows an attribute".format(name))
            self.__dict__[name] = value

    def __getattr__(self, attr):
        result = self.lookup.get(attr)
        if result is None:
            raise AttributeError('enumeration has no member {}'.format(attr))
        return result

    def whatis(self, value):
        return self.reverseLookup[value]
