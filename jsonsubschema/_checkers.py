'''
Created on June 24, 2019
@author: Andrew Habib
'''

import copy
import json
import math
import numbers
import sys

import intervals as I
from greenery.lego import parse

import jsonsubschema.config as config

import jsonsubschema._constants as definitions
import jsonsubschema._utils as utils
from jsonsubschema._utils import print_db


class UninhabitedMeta(type):

    def __call__(cls, *args, **kwargs):
        obj = type.__call__(cls, *args, **kwargs)
        obj.updateInternalState()
        obj.isUninhabited()
        utils.validate_schema(obj)
        return obj


class JSONschema(dict, metaclass=UninhabitedMeta):

    # kw_defaults = {}

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)
        # Instead of adding enum at every child constructor,
        # do it here once and fir all.
        if "enum" in self:
            self.enum = self["enum"]

    def updateInternalState(self):
        pass

    def updateKeys(self):
        for k, v in self.kw_defaults.items():
            if k not in self.keys():
                self[k] = v
        # dirty hack becuase self.items() is already an attribute of dict
        if "items" in self.keys():
            self["items_"] = self["items"]
            del self["items"]

    def isBoolean(self):
        return self.keys() & definitions.Jconnectors

    def hasEnum(self):
        return "enum" in self.keys() or hasattr(self, "enum")

    def isUninhabited(self):
        # Don't store uninhabited key,
        # but rather re-check on the fly to
        # get an updated results based on the
        # current internal state.
        uninhabited = self._isUninhabited() and (
            "enum" in self and not self["enum"])
        if config.WARN_UNINHABITED and uninhabited:
            print("Found an uninhabited type at: ", type(self), self)
        return uninhabited

    def meet(self, s):
        #
        if self == s or is_top(s):
            return self
        #
        if is_top(self):
            return s
        #
        if is_bot(self) or is_bot(s):
            return JSONbot()
        #
        ret = self._meet(s)
        #
        if self.hasEnum() or s.hasEnum():
            enum = JSONschema.meet_enum(self, s)
            if enum:
                ret["enum"] = list(enum)
                ret.enum = ret["enum"]
            # instead of returning uninhabited type, return bot
            else:
                return JSONbot()
        #
        return ret

    @staticmethod
    def meet_enum(s1, s2):
        enum = set(s1.get("enum", [])) | set(s2.get("enum", []))
        valid_enum1 = utils.get_valid_enum_vals(enum, s1)
        valid_enum2 = utils.get_valid_enum_vals(enum, s2)
        return set(valid_enum1) & set(valid_enum2)

    def meet_handle_rhs(self, s, meet_cb):
        #
        if s.type == "anyOf":
            return JSONanyOf._meetAnyOf(s, self)
        #
        else:
            return meet_cb(self, s)

    def join(self, s):
        #
        if self == s or is_bot(s):
            return self
        #
        if is_bot(self):
            return s
        #
        if is_top(self) or is_top(s):
            return JSONtop()
        #
        ret = self._join(s)
        # instead of returning uninhabited types, return bot
        if is_bot(ret):
            return JSONbot()
        else:
            return ret

    def isSubtype(self, s):
        #
        if self == s or is_bot(self) or is_top(s):
            return True
        #
        if (not is_bot(self) and is_bot(s)) \
                or (is_top(self) and not is_top(s)):
            return False
        #
        return self.subtype_enum(s) and self._isSubtype(s)

    def subtype_enum(self, s):
        if self.hasEnum():
            valid_enum = utils.get_valid_enum_vals(self.enum, s)
            # no need to check individual elements
            # as enum values are unique by definition
            if len(valid_enum) == len(self.enum):
                return True
            else:
                return False
        else:
            return True

    def isSubtype_handle_rhs(self, s, isSubtype_cb):

        if s.isBoolean():
            # TODO revisit all of this. They are wrong.
            if s.type == "anyOf":
                return any(isSubtype_cb(self, i) for i in s.anyOf)
            elif s.type == "allOf":
                return all(isSubtype_cb(self, i) for i in s.allOf)
            elif s.type == "oneOf":
                return utils.one(isSubtype_cb(self, i) for i in s.oneOf)
            elif s.type == "not":
                # TODO
                print("No handling of 'not' on rhs yet.")
                return None
        else:
            return isSubtype_cb(self, s)


class JSONtop(JSONschema):
    def __init__(self):
        super().__init__({})
        self.type = "top"

    def _isUninhabited(self):
        return False

    def _meet(self, s):
        return s

    def _isSubtype(self, s):

        def _isTopSubtype(s1, s2):
            if is_top(s2):
                return True
            return False

        super().isSubtype_handle_rhs(s, _isTopSubtype)

    def __eq__(self, s):
        if is_top(s):
            return True
        else:
            return False

    def __repr__(self):
        return "JSON_TOP"

    def __bool__(self):
        return True


def is_top(obj):
    return obj == True or obj == {} or isinstance(obj, JSONtop)


class JSONbot(JSONschema):
    def __init__(self):
        super().__init__({"not": {}})
        self.type = "bot"

    def _isUninhabited(self):
        return True

    def _meet(self, s):
        return self

    def _isSubtype(self, s):

        def _isBotSubtype(s1, s2):
            if is_bot(s2):
                return True
            return False

        super().isSubtype_handle_rhs(s, _isBotSubtype)

    def __eq__(self, s):
        if is_bot(s):
            return True
        else:
            return False

    def __repr__(self):
        return "JSON_BOT"

    def __bool__(self):
        return False


def is_bot(obj):
    return obj == False \
        or (utils.is_dict(obj) and obj.get("not") == {}) \
        or isinstance(obj, JSONbot) \
        or (isinstance(obj, JSONschema) and obj.isUninhabited())


class JSONTypeString(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = self["type"] = "string"
        self.minLength = self.get("minLength", 0)
        self.maxLength = self.get("maxLength", I.inf)
        # json regexes are not anchored but the greenery library we use
        # for regex inclusion assumes anchored regexes. So
        # pad the regex with '.*' from both sides.
        self.pattern = utils.regex_unanchor(
            self["pattern"]) if "pattern" in self else ".*"

    def _isUninhabited(self):
        return (self.minLength > self.maxLength) or self.pattern == None

    def updateInternalState(self):
        self.interval = I.closed(self.minLength, self.maxLength)

    def _meet(self, s):

        def _meetString(s1, s2):
            if s2.type == "string":
                ret = {}
                mn = max(s1.minLength, s2.minLength)
                if utils.is_num(mn):
                    ret["minLength"] = mn
                mx = min(s1.maxLength, s2.maxLength)
                if utils.is_num(mx):
                    ret["maxLength"] = mx
                # Explicitly anchor pattern when assigned to the json key
                # to reflect the greenery lib behavior on the json object.
                ret["pattern"] = "^" + \
                    utils.regex_meet(s1.pattern, s2.pattern) + "$"
                return JSONTypeString(ret)
            else:
                return JSONbot()

        return super().meet_handle_rhs(s, _meetString)

    def _isSubtype(self, s):

        def _isStringSubtype(s1, s2):
            if s2.type == "string":
                is_sub_interval = s1.interval in s2.interval
                if not is_sub_interval:
                    return False
                #
                # at this point, length is compatible,
                # so we should now worry about pattern only.
                if s2.pattern == None or s2.pattern == "":
                    return True
                elif s1.pattern == None or s1.pattern == "":
                    return False
                elif s1.pattern == s2.pattern:
                    return True
                else:
                    if utils.regex_isSubset(s1.pattern, s2.pattern):
                        return True
                    else:
                        return False
            else:
                return False

        return super().isSubtype_handle_rhs(s, _isStringSubtype)

    @staticmethod
    def negString(s):
        negated_strings = []

        if "minLength" in s:
            negated_strings.append(JSONTypeString(
                {"maxLength": s.minLength - 1}))
        if "maxLength" in s:
            negated_strings.append(JSONTypeString(
                {"minLength": s.maxLength + 1}))
        if "pattern" in s:
            # Explicitly anchor pattern when assigned to the json key
            # to reflect the greenery lib behavior on the json object.
            negated_strings.append(JSONTypeString(
                {"pattern": "^" + utils.complement_of_string_pattern(s.pattern) + "$"}))

        if len(negated_strings) == 0:
            return None
        else:
            return JSONanyOf({"anyOf": negated_strings})


def isNumericUninhabited(s):
    return s.interval.is_empty()  \
        or (s.multipleOf != None and s.multipleOf not in s.interval
            and s.interval.lower != -I.inf and s.interval.upper != I.inf)


class JSONTypeInteger(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = self["type"] = "integer"
        self.minimum = self.get("minimum", -I.inf)
        self.maximum = self.get("maximum", I.inf)
        self.exclusiveMinimum = self.get("exclusiveMinimum", False)
        self.exclusiveMaximum = self.get("exclusiveMaximum", False)
        self.multipleOf = self.get("multipleOf", None)

    def build_interval_draft4(self):
        if self.exclusiveMinimum and self.exclusiveMaximum:
            self.interval = I.closed(self.minimum+1, self.maximum-1)
        elif self.exclusiveMinimum:
            self.interval = I.closed(self.minimum+1, self.maximum)
        elif self.exclusiveMaximum:
            self.interval = I.closed(self.minimum, self.maximum-1)
        else:
            self.interval = I.closed(self.minimum, self.maximum)

    def _isUninhabited(self):
        return isNumericUninhabited(self)

    def updateInternalState(self):
        self.build_interval_draft4()

    def _meet(self, s):

        def _meetInteger(s1, s2):
            if s2.type in definitions.Jnumeric:
                ret = {}
                ret["type"] = "integer"

                mn = max(s1.minimum, s2.minimum)
                if utils.is_num(mn):
                    ret["minimum"] = mn

                mx = min(s1.maximum, s2.maximum)
                if utils.is_num(mx):
                    ret["maximum"] = mx

                mulOf = utils.lcm(s1.multipleOf, s2.multipleOf)
                if mulOf:
                    ret["multipleOf"] = mulOf

                return JSONTypeInteger(ret)
            else:
                return JSONbot()

        return super().meet_handle_rhs(s, _meetInteger)

    def _isSubtype(self, s):

        def _isIntegerSubtype(s1, s2):
            if s2.type in definitions.Jnumeric:
                #
                is_sub_interval = s1.interval in s2.interval
                if not is_sub_interval:
                    print_db("num__00")
                    return False
                #
                if (s1.multipleOf == s2.multipleOf) \
                        or (s1.multipleOf != None and s2.multipleOf == None) \
                        or (s1.multipleOf != None and s2.multipleOf != None and s1.multipleOf % s2.multipleOf == 0) \
                        or (s1.multipleOf == None and s2.multipleOf == 1):
                    print_db("num__01")
                    return True
            else:
                return False

        return super().isSubtype_handle_rhs(s, _isIntegerSubtype)

    @staticmethod
    def negInteger(s):
        negated_int = []
        # We will always ignore setting exclusiveMin/Max and
        # instead, capture it in the min/max value directly.
        # for k, default in JSONTypeInteger.kw_defaults.items():
        #     if s.__getattr__(k) != default:
        # if k == "minimum":
        #     if s.__getattr__("exclusiveMinimum"):
        #         negated_int.append(JSONTypeInteger({"maximum": s.__getattr__(k)}))
        #         negated_int.append(JSONTypeNumber({"maximum": s.__getattr__(k)}))
        #         negated_int.append(JSONTypeNumber({"minimum": s.__getattr__(k), "multipleOf": 1, "exclusiveMinimum": True}))

        #     else:
        #         negated_int.append(JSONTypeInteger({"maximum": s.__getattr__(k) - 1}))
        #         negated_int.append(JSONTypeNumber({"maximum": s.__getattr__(k), "multipleOf": 1}))
        #         negated_int.append(JSONTypeNumber({"minimum": s.__getattr__(k), "multipleOf": 1}))
        #
        #
        # if k == "minimum":
        #     if s.__getattr__("exclusiveMinimum"):
        #         negated_int.append({"type": "integer", "maximum": s.__getattr__(k)})
        #         negated_int.append({"type": "number", "maximum": s.__getattr__(k)})
        #         negated_int.append({"allOf": [{"type": "number"} ,{"not": {"type": "number", "minimum": s.__getattr__(k), "multipleOf": 1, "exclusiveMinimum": True}}]})

        #     else:
        #         negated_int.append({"type": "integer", "maximum": s.__getattr__(k) - 1})
        #         negated_int.append({"type": "number", "maximum": s.__getattr__(k), "exclusiveMinimum": True})
        #         negated_int.append({"allOf": [{"type": "number"} ,{"not": {"type": "number", "minimum": s.__getattr__(k), "multipleOf": 1, "exclusiveMinimum": False}}]})
        # elif k == "maximum":
        #     if s.__getattr__("exclusiveMaximum"):
        #         negated_int.append(JSONTypeInteger({"minimum": s.__getattr__(k)}))
        #         negated_int.append(JSONTypeNumber({"minimum": s.__getattr__(k), "multipleOf": 1}))
        #     else:
        #         negated_int.append(JSONTypeInteger({"minimum": s.__getattr__(k) + 1}))
        #         negated_int.append(JSONTypeNumber({"minimum": s.__getattr__(k) + 1, "multipleOf": 1}))

        # if len(negated_int) == 0:
        #     return JSONTypeNumber({"not": {"multipleOf": 1}})
        # else:
        #     return JSONanyOf({"anyOf": negated_int})
        # import jsonsubschema._canonicalization as c
        # return c.canonicalize_connectors({"andOf": negated_int})

        return None


class JSONTypeNumber(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = self["type"] = "number"
        self.minimum = self.get("minimum", -I.inf)
        self.maximum = self.get("maximum", I.inf)
        self.exclusiveMinimum = self.get("exclusiveMinimum", False)
        self.exclusiveMaximum = self.get("exclusiveMaximum", False)
        self.multipleOf = self.get("multipleOf", None)

    def build_interval_draft4(self):
        if self.exclusiveMinimum and self.exclusiveMaximum:
            self.interval = I.open(self.minimum, self.maximum)
        elif self.exclusiveMinimum:
            self.interval = I.openclosed(self.minimum, self.maximum)
        elif self.exclusiveMaximum:
            self.interval = I.closedopen(self.minimum, self.maximum)
        else:
            self.interval = I.closed(self.minimum, self.maximum)

    def _isUninhabited(self):
        return isNumericUninhabited(self)

    def updateInternalState(self):
        self.build_interval_draft4()

    def _meet(self, s):

        def _meetNumber(s1, s2):
            if s2.type in definitions.Jnumeric:
                ret = {}

                mn = max(s1.minimum, s2.minimum)
                if utils.is_num(mn):
                    ret["minimum"] = mn

                mx = min(s1.maximum, s2.maximum)
                if utils.is_num(mx):
                    ret["maximum"] = mx

                mulOf = utils.lcm(s1.multipleOf, s2.multipleOf)
                if mulOf:
                    ret["multipleOf"] = mulOf

                if s2.type == "integer":
                    ret["type"] = "integer"
                    return JSONTypeInteger(ret)
                else:
                    ret["type"] = "number"
                    return JSONTypeNumber(ret)
            else:
                return JSONbot()

        return super().meet_handle_rhs(s, _meetNumber)

    def _isSubtype(self, s):

        def _isNumberSubtype(s1, s2):
            if s2.type == "number":
                is_sub_interval = s1.interval in s2.interval
                if not is_sub_interval:
                    print_db("num__00")
                    return False
                #
                if (s1.multipleOf == s2.multipleOf) \
                        or (s1.multipleOf != None and s2.multipleOf == None) \
                        or (s1.multipleOf != None and s2.multipleOf != None and s1.multipleOf % s2.multipleOf == 0) \
                        or (utils.is_int_equiv(s1.multipleOf) and s2.multipleOf == None):
                    print_db("num__01")
                    return True
            elif s2.type == "integer":
                is_sub_interval = s1.interval in s2.interval
                if not is_sub_interval:
                    print_db("num__02")
                    return False
                #
                if utils.is_int_equiv(s1.multipleOf) and \
                        (s2.multipleOf == None or ((s1.multipleOf != None and s2.multipleOf != None and s1.multipleOf % s2.multipleOf == 0))):
                    print_db("num__03")
                    return True
            else:
                print_db("num__04")
                return False

        return super().isSubtype_handle_rhs(s, _isNumberSubtype)

    @staticmethod
    def negNumber(s):
        # for k, default in JSONTypeNumber.kw_defaults.items():
        #     if s.__getattr__(k) != default:
        #         break
        # else:
        return None


class JSONTypeBoolean(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = self["type"] = "boolean"

    def _isUninhabited(self):
        return False

    def _meet(self, s):

        def _meetBoolean(s1, s2):
            if s2.type == "boolean":
                return s1
            else:
                return JSONbot()

        return super().meet_handle_rhs(s, _meetBoolean)

    def _isSubtype(self, s):

        def _isBooleanSubtype(self, s2):
            if s2.type == "boolean":
                return True
            else:
                return False

        return super().isSubtype_handle_rhs(s, _isBooleanSubtype)

    @staticmethod
    def negBoolean(s):
        return None


class JSONTypeNull(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = self["type"] = "null"

    def _isUninhabited(self):
        return False

    def _meet(self, s):

        def _meetNull(s1, s2):

            if s2.type == "null":
                return s1
            else:
                return JSONbot()

        return super().meet_handle_rhs(s, _meetNull)

    def _isSubtype(self, s):

        def _isNullSubtype(self, s2):
            if s2.type == "null":
                return True
            else:
                return False

        return super().isSubtype_handle_rhs(s, _isNullSubtype)

    @staticmethod
    def negNull(s):
        return None


class JSONTypeArray(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = self["type"] = "array"
        self.minItems = self.get("minItems", 0)
        self.maxItems = self.get("maxItems", I.inf)
        self.items_ = self.get("items", JSONtop())
        self.additionalItems = self.get("additionalItems", True)
        self.uniqueItems = self.get("uniqueItems", False)

    def compute_actual_maxItems(self):
        if utils.is_list(self.items_) and is_bot(self.additionalItems):
            new_max = min(self.maxItems, len(self.items_))
            if new_max != self.maxItems:
                self.maxItems = new_max

    def _isUninhabited(self):
        return (self.minItems > self.maxItems) or \
            (utils.is_list(self.items_) and self.additionalItems ==
             False and self.minItems > len(self.items_)) or \
            (utils.is_list(self.items_) and len(self.items_) == 0)

    def updateInternalState(self):
        self.compute_actual_maxItems()
        self.interval = I.closed(self.minItems, self.maxItems)

    def _meet(self, s):

        def _meetArray(s1, s2):
            if s2.type == "array":
                # ret = {}
                # ret["type"] = "array"
                # ret["minItems"] = max(s1.minItems, s2.minItems)
                # ret["maxItems"] = min(s1.maxItems, s2.maxItems)
                # ret["uniqueItems"] = s1.uniqueItems or s2.uniqueItems
                ret = JSONTypeArray({})
                # ret["type"] = "array"
                ret.minItems = max(s1.minItems, s2.minItems)
                ret.maxItems = min(s1.maxItems, s2.maxItems)
                ret.uniqueItems = s1.uniqueItems or s2.uniqueItems

                def meet_arrayItems_dict_list(s1, s2, ret):
                    assert utils.is_dict(s1.items_) and utils.is_list(
                        s2.items_), "Violating meet_arrayItems_dict_list condition: 's1.items is dict' and 's2.items is list'"

                    itms = []
                    for i in s2.items_:
                        r = i.meet(s1.items_)
                        if not (is_bot(r) or r.isUninhabited()):
                            itms.append(r)
                        else:
                            break

                    ret.items_ = itms

                    if s2.additionalItems == True:
                        ret.additionalItems = copy.deepcopy(s1.items_)
                    elif s2.additionalItems == False:
                        ret.additionalItems = False
                    elif utils.is_dict(s2.additionalItems):
                        addItms = s2.additionalItems.meet(s1.items_)
                        ret.additionalItems = False if is_bot(
                            addItms) else addItms
                    return ret

                if utils.is_dict(s1.items_):

                    if utils.is_dict(s2.items_):
                        ret.items = s1.items_.meet(s2.items_)

                    elif utils.is_list(s2.items_):
                        ret = meet_arrayItems_dict_list(s1, s2, ret)

                elif utils.is_list(s1.items_):

                    if utils.is_dict(s2.items_):
                        ret = meet_arrayItems_dict_list(s2, s1, ret)

                    elif utils.is_list(s2.items_):
                        self_len = len(s1.items_)
                        s_len = len(s2.items_)

                        def meet_arrayAdditionalItems_list_list(s1, s2):
                            if utils.is_bool(s1.additionalItems) and utils.is_bool(s2.additionalItems):
                                ad = s1.additionalItems and s2.additionalItems
                            elif utils.is_dict(s1.additionalItems):
                                ad = s1.additionalItems.meet(
                                    s2.additionalItems)
                            elif utils.is_dict(s2.additionalItems):
                                ad = s2.additionalItems.meet(
                                    s1.additionalItems)
                            return False if is_bot(ad) else ad

                        def meet_array_longlist_shorterlist(s1, s2, ret):
                            s1_len = len(s1.items_)
                            s2_len = len(s2.items_)
                            assert s1_len > s2_len, "Violating meet_array_longlist_shorterlist condition: 's1.len > s2.len'"
                            itms = []
                            for i, j in zip(s1.items_, s2.items_):
                                r = i.meet(j)
                                if not (is_bot(r) or r.isUninhabited()):
                                    itms.append(r)
                                else:
                                    ad = False
                                    break
                            else:
                                for i in range(s2_len, s1_len):
                                    r = s1.items_[i].meet(s2.additionalItems)
                                    if not (is_bot(r) or r.isUninhabited()):
                                        itms.append(r)
                                    else:
                                        ad = False
                                        break
                                else:
                                    ad = meet_arrayAdditionalItems_list_list(
                                        s1, s2)

                            ret.additionalItems = ad
                            ret.items_ = itms
                            return ret

                        if self_len == s_len:
                            itms = []
                            for i, j in zip(s1.items_, s2.items_):
                                r = i.meet(j)
                                if not (is_bot(r) or r.isUninhabited()):
                                    itms.append(r)
                                else:
                                    ad = False
                                    break
                            else:
                                ad = meet_arrayAdditionalItems_list_list(
                                    s1, s2)

                            ret.additionalItems = ad
                            ret.items_ = itms

                        elif self_len > s_len:
                            ret = meet_array_longlist_shorterlist(s1, s2, ret)

                        elif self_len < s_len:
                            ret = meet_array_longlist_shorterlist(s2, s1, ret)
                ret.updateInternalState()
                return ret

            else:
                return JSONbot()

        return super().meet_handle_rhs(s, _meetArray)

    def _isSubtype(self, s):

        def _isArraySubtype(s1, s2):
            if s2.type != "array":
                return False
            #
            # -- minItems and maxItems
            is_sub_interval = s1.interval in s2.interval
            if not is_sub_interval:
                print_db("__01__")
                return False
            #
            # -- uniqueItemsue
            # TODO Double-check. Could be more subtle?
            if not s1.uniqueItems and s2.uniqueItems:
                print_db("__02__")
                return False
            #
            # -- items = {not empty}
            # no need to check additionalItems
            if utils.is_dict(s1.items_):
                if utils.is_dict(s2.items_):
                    print_db(s1.items_)
                    print_db(s2.items_)
                    if s1.items_.isSubtype(s2.items_):
                        print_db("__05__")
                        return True
                    else:
                        print_db("__06__")
                        return False
                elif utils.is_list(s2.items_):
                    if s2.additionalItems == False:
                        print_db("__07__")
                        return False
                    elif s2.additionalItems == True:
                        for i in s2.items_:
                            if not s1.items_.isSubtype(i):
                                print_db("__08__")
                                return False
                        print_db("__09__")
                        return True
                    elif utils.is_dict(s2.additionalItems):
                        for i in s2.items_:
                            if not s1.items_.isSubtype(i):
                                print_db("__10__")
                                return False
                        print_db(type(s1.items_), s1.items_)
                        print_db(type(s2.additionalItems),
                                 s2.additionalItems)
                        if s1.items_.isSubtype(s2.additionalItems):
                            print_db("__11__")
                            return True
                        else:
                            print_db("__12__")
                            return False
            #
            elif utils.is_list(s1.items_):
                print_db("lhs is list")
                if utils.is_dict(s2.items_):
                    if s1.additionalItems == False:
                        for i in s1.items_:
                            if not i.isSubtype(s2.items_):
                                print_db("__13__")
                                return False
                        print_db("__14__")
                        return True
                    elif s1.additionalItems == True:
                        for i in s1.items_:
                            if not i.isSubtype(s2.items_):
                                return False
                            # since s1.additional items is True,
                            # then TOP should also be a subtype of
                            # s2.items
                        if JSONtop().isSubtype(s2.items_):
                            return True
                        return False
                    elif utils.is_dict(s1.additionalItems):
                        for i in s1.items_:
                            if not i.isSubtype(s2.items_):
                                return False
                        if s1.additionalItems.isSubtype(s2.items_):
                            return True
                        else:
                            return False
                # now lhs and rhs are lists
                elif utils.is_list(s2.items_):
                    print_db("lhs & rhs are lists")
                    len1 = len(s1.items_)
                    len2 = len(s2.items_)
                    for i, j in zip(s1.items_, s2.items_):
                        if not i.isSubtype(j):
                            return False
                    if len1 == len2:
                        print_db("len1 == len2")
                        if s1.additionalItems == s2.additionalItems:
                            return True
                        elif s1.additionalItems == True and s2.additionalItems == False:
                            return False
                        elif s1.additionalItems == False and s2.additionalItems == True:
                            return True
                        else:
                            return s1.additionalItems.isSubtype(s2.additionalItems)
                    elif len1 > len2:
                        diff = len1 - len2
                        for i in range(len1-diff, len1):
                            if s2.additionalItems == False:
                                return False
                            elif s2.additionalItems == True:
                                return True
                            elif not s1.items_[i].isSubtype(s2.additionalItems):
                                print_db("9999")
                                return False
                        print_db("8888")
                        return True
                    else:  # len2 > len 1
                        diff = len2 - len1
                        for i in range(len2 - diff, len2):
                            if s1.additionalItems == False:
                                return True
                            elif s1.additionalItems == True:
                                return False
                            elif not s1.additionalItems.isSubtype(s2.items_[i]):
                                return False
                        return s1.additionalItems.isSubtype(s2.additionalItems)

        return super().isSubtype_handle_rhs(s, _isArraySubtype)

    @staticmethod
    def negArray(s):
        # for k, default in JSONTypeArray.kw_defaults.items():
        #     if s.__getattr__(k) != default:
        #         break
        # else:
        return None


class JSONTypeObject(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = self["type"] = "object"
        self.properties = self.get("properties", {})
        self.additionalProperties = self.get("additionalProperties", JSONtop())
        self.required = self.get("required", [])
        self.minProperties = self.get("minProperties", 0)
        self.maxProperties = self.get("maxProperties", I.inf)
        self.patternProperties = {}
        if "patternProperties" in self:
            for k, v in self["patternProperties"].items():
                self.patternProperties[utils.regex_unanchor(k)] = v

    def compute_actual_min_max_Properties(self):

        new_min = max(self.minProperties, len(self.required))
        if new_min != self.minProperties:
            self.minProperties = new_min

        # if is_bot(self.additionalProperties): # This is wrong because of patternProperties
        #     new_max = min(self.maxProperties, len(self.properties))
        #     if new_max != self.maxProperties:
        #         self.maxProperties = new_max

    def _isUninhabited(self):

        def required_is_uninhabited(s):
            ''' checks if every required key is actually allowed 
                by the key restrictions '''
            if s.additionalProperties:
                return False

            for k in s.required:
                if not k in s.properties.keys():
                    for k_ in s.patternProperties.keys():
                        if utils.regex_matches_string(k_, k):
                            break
                    else:
                        # here, inner loop finished and key was not found;
                        # so it is uninhabited because a required key is not allowed
                        return True

            return False

        return self.minProperties > self.maxProperties \
            or len(self.required) > self.maxProperties \
            or required_is_uninhabited(self)

    def updateInternalState(self):
        self.compute_actual_min_max_Properties()
        self.interval = I.closed(self.minProperties, self.maxProperties)
        #
        # if self.patternProperties != self.kw_defaults["patternProperties"]:
        #     p = {}
        #     for k in list(self.patternProperties.keys()):
        #         v = self.patternProperties.pop(k)
        #         new_k = utils.regex_unanchor(k)
        #         p[new_k] = v
        #     for k in p.keys():
        #         self.patternProperties[k] = p[k]

    def _meet(self, s):

        def _meetObject(s1, s2):
            if s2.type == "object":
                ret = JSONTypeObject({})
                ret.required = list(set(s1.required).union(s2.required))
                ret.minProperties = max(s1.minProperties, s2.minProperties)
                ret.maxProperties = min(s1.maxProperties, s2.maxProperties)
                #
                if utils.is_bool(s1.additionalProperties) and utils.is_bool(s2.additionalProperties):
                    ad = s1.additionalProperties and s2.additionalProperties
                elif utils.is_dict(s1.additionalProperties):
                    ad = s1.additionalProperties.meet(
                        s2.additionalProperties)
                elif utils.is_dict(s2.additionalProperties):
                    ad = s2.additionalProperties.meet(
                        s1.additionalProperties)
                ret.additionalProperties = False if is_bot(ad) else ad
                #
                # For meet of properties and patternProperties,
                # no need to check whether a key is valid against  patternProperties of the other schema
                # or to calculate intersections among patternProperties of both schemas
                # cuz the validator takes care of this during validation of actual instances.
                # For efficiency, we just include all key in properties and patternProperties of both schemas.
                # We only have to handle exactly matching keys in both properties and patternProperties.
                #
                properties = {}
                for k in s1.properties.keys():
                    if k in s2.properties.keys():
                        properties[k] = s1.properties[k].meet(s2.properties[k])
                    else:
                        properties[k] = s1.properties[k]
                for k in s2.properties.keys():
                    if k not in s1.properties.keys():
                        properties[k] = s2.properties[k]
                ret.properties = properties
                #
                pProperties = {}
                for k in s1.patternProperties.keys():
                    if k in s2.patternProperties.keys():
                        pProperties[k] = s1.patternProperties[k].meet(
                            s2.patternProperties[k])
                    else:
                        pProperties[k] = s1.patternProperties[k]
                for k in s2.patternProperties.keys():
                    if k not in s1.patternProperties.keys():
                        pProperties[k] = s2.patternProperties[k]
                ret.patternProperties = pProperties
                #
                ret.updateInternalState()
                return ret
            else:
                return JSONbot()

        return super().meet_handle_rhs(s, _meetObject)

    def _isSubtype(self, s):

        def _isObjectSubtype(s1, s2):
            ''' The general intuition is that a json object with more keys is more restrictive 
                than a similar object with fewer keys. 

                E.g.: if corresponding keys have same schemas, then 
                {name: {..}, age: {..}} <: {name: {..}}
                {name: {..}, age: {..}} />: {name: {..}}

                So the subtype checking is divided into two major parts:
                I) lhs keys/patterns/additional should be a superset of rhs
                II) schemas of comparable keys should have lhs <: rhs
            '''
            if s2.type != "object":
                return False

            # Check properties range
            is_sub_interval = s1.interval in s2.interval
            if not is_sub_interval:
                print_db("__00__")
                return False
            #
            else:
                # If ranges are ok, check another trivial case of almost identical objects.
                # This is some sort of performance heuristic.
                if set(s1.required).issuperset(s2.required) \
                    and s1.properties == s2.properties \
                    and s1.patternProperties == s2.patternProperties \
                    and (s1.additionalProperties == s2.additionalProperties
                         or (utils.is_dict(s1.additionalProperties)
                             and s1.additionalProperties.isSubtype(s2.additionalProperties))):
                    print_db("__01__")
                    return True
            #

            def get_schema_for_key(k, s):
                ''' Searches for matching key and get the corresponding schema(s).
                    Returns iterable because if a key matches more than one pattern, 
                    that key schema has to match all corresponding patterns schemas.
                '''
                if k in s.properties.keys():
                    return [k.properties[k]]
                else:
                    ret = []
                    for k_ in s.patternProperties.keys():
                        if utils.regex_matches_string(k_, k):
                            # in case a key has to be checked against patternProperties,
                            # it has to adhere to all schemas which have pattern matching the key.
                            ret.append(k.patternProperties[k_])
                    if ret:
                        return ret

                return [s.additionalProperties]

            # Check that required keys satisfy subtyping.
            # lhs required keys should be superset of rhs required keys.
            if not set(s1.required).issuperset(s2.required):
                print_db("__02__")
                return False
            # If required keys are properly defined, check their corresponding
            # schemas and make sure they are subtypes.
            # This is required because you could have a required key which does not
            # have an explicit schema defined by the json object.

            else:
                for k in set(s1.required).intersection(s2.required):
                    for lhs_ in get_schema_for_key(k, s1):
                        for rhs_ in get_schema_for_key(k, s2):
                            if lhs_:
                                if rhs_:
                                    if not lhs_.isSubtype(rhs_):
                                        print_db("__03__")
                                        return False
                                else:
                                    print_db("__04__")
                                    return False


            extra_keys_on_rhs = set(s2.properties.keys()).difference(
                s1.properties.keys())
            for k in extra_keys_on_rhs.copy():
                for k_ in s1.patternProperties.keys():
                    if utils.regex_matches_string(k_, k):
                        extra_keys_on_rhs.remove(k)
            if extra_keys_on_rhs:
                if not s1.additionalProperties:
                    print_db("__05__")
                    return False
                else:
                    for k in extra_keys_on_rhs:
                        if not s1.additionalProperties.isSubtype(s2.properties[k]):
                            print_db("__06__")
                            return False

            extra_patterns_on_rhs = set(s2.patternProperties.keys()).difference(
                s1.patternProperties.keys())
            for k in extra_patterns_on_rhs.copy():
                for k_ in s1.patternProperties.keys():
                    if utils.regex_isSubset(k, k_):
                        extra_patterns_on_rhs.remove(k)
            if extra_patterns_on_rhs:
                if not s1.additionalProperties:
                    print_db("__07__")
                    return False
                else:
                    for k in extra_patterns_on_rhs:
                        if not s1.additionalProperties.isSubtype(s2.patternProperties[k]):
                            try:  # means regex k is infinite
                                parse(k).cardinality()
                            except OverflowError:
                                print_db("__08__")
                                return False
            #
            # missing_props_from_lhs = set(
            #     s2.properties.keys()) - set(s1.properties.keys())
            # for k in missing_props_from_lhs:
            #     for k_ in s1.patternProperties.keys():
            #         if utils.regex_matches_string(k_, k):
            #             if not s1.patternProperties[k_].isSubtype(s2.properties[k]):
            #                 return False

                        # Now, lhs has a patternProperty which is subtype of a property on the rhs.
                        # Idealy, at this point, I'd like to check that EVERY property matched by
                        # this pattern also exist on the rhs.
                        # from greenery.lego import parse
                        # p = parse(k_)
                        # try:
                            # p.cardinality

            # first, matching properties should be subtype pairwise
            unmatched_lhs_props_keys = set(s1.properties.keys())
            for k in s1.properties.keys():
                if k in s2.properties.keys():
                    unmatched_lhs_props_keys.discard(k)
                    if not s1.properties[k].isSubtype(s2.properties[k]):
                        return False
                # for the remaining keys, make sure they either don't exist
                # in rhs or if they, then their schemas should be sub-type
                else:
                    for k_ in s2.patternProperties:
                        # if utils.regex_isSubset(k, k_):
                        if utils.regex_matches_string(k_, k):
                            unmatched_lhs_props_keys.discard(k)
                            if not s1.properties[k].isSubtype(s2.patternProperties[k_]):
                                return False

            # second, matching patternProperties should be subtype pairwise
            unmatched_lhs_pProps_keys = set(s1.patternProperties.keys())
            for k in s1.patternProperties.keys():
                for k_ in s2.patternProperties.keys():
                    if utils.regex_isSubset(k_, k):
                        unmatched_lhs_pProps_keys.discard(k)
                        if not s1.patternProperties[k].isSubtype(s2.patternProperties[k_]):
                            return False
            # third,

            # fourth,
            if s2.additionalProperties == True:
                return True
            elif s2.additionalProperties == False:
                if s1.additionalProperties == True:
                    return False
                elif unmatched_lhs_props_keys or unmatched_lhs_pProps_keys:
                    return False
                else:
                    return True
            else:
                for k in unmatched_lhs_props_keys:
                    if not s1.properties[k].isSubtype(s2.additionalProperties):
                        return False
                for k in unmatched_lhs_pProps_keys:
                    if not s1.patternProperties[k].isSubtype(s2.additionalProperties):
                        return False
                if s1.additionalProperties == True:
                    return False
                elif s1.additionalProperties == False:
                    return True
                else:
                    return s1.additionalProperties.isSubtype(s2.additionalProperties)

        return super().isSubtype_handle_rhs(s, _isObjectSubtype)

    @staticmethod
    def negObject(s):
        # for k, default in JSONTypeObject.kw_defaults.items():
        #     if s.__getattr__(k) != default:
        #         break
        # else:
        return None


def JSONanyOfFactory(s):
    ret = JSONbot()
    for i in s.get("anyOf"):
        ret = ret.join(i)

    return ret


class JSONanyOf(JSONschema):

    def __init__(self, s):
        super().__init__(s)
        self.type = "anyOf"
        self.anyOf = self.get("anyOf")

    def __eq__(self, other):
        if isinstance(other, JSONanyOf):
            return tuple(sorted(d.items()) for d in self.anyOf) == tuple(sorted(d.items() for d in other.anyOf))
        else:
            return super().__eq__(other)

    def updateInternalState(self):
        for d_i in self.anyOf:
            if "anyOf" in d_i.keys():
                self.anyOf.extend(d_i.get("anyOf"))
                self.anyOf.remove(d_i)

    def _isUninhabited(self):
        return all(is_bot(i) for i in self.anyOf)

    def _meet(self, s):

        return super().meet_handle_rhs(s, JSONanyOf._meetAnyOf)

    @staticmethod
    def _meetAnyOf(s1, s2):
        anyofs = []
        for i in s1.anyOf:
            tmp = i.meet(s2)
            if not is_bot(tmp):
                anyofs.append(tmp)

        if len(anyofs) > 1:
            return JSONanyOf({"anyOf": anyofs})
        elif len(anyofs) == 1:
            return anyofs.pop()
        else:
            return JSONbot()

    def _isSubtype(self, s):

        def _isAnyofSubtype(s1, s2):
            for s in s1.anyOf:
                if not s.isSubtype(s2):
                    return False
            return True

        return _isAnyofSubtype(self, s)


def JSONallOfFactory(s):
    ret = JSONtop()
    for i in s.get("allOf"):
        ret = ret.meet(i)

    return ret


class JSONoneOf(JSONschema):

    def __init__(self, s):
        super().__init__(s)


    def _isUninhabited(self):
        return all(is_bot(i) for i in self.oneOf)

    def _meet(self, s):
        pass

    def _isSubtype(self, s):
        sys.exit("oneOf on the lhs is not supported yet.")


def JSONnotFactory(s):
    t = s.type
    if t in definitions.Jtypes:
        anyofs = []
        for t_i in definitions.Jtypes - set([t]):
            anyofs.append(typeToConstructor.get(t_i)({"type": t_i}))
        anyofs.append(negTypeToConstructor.get(t)(s))
        return JSONanyOf({"anyOf": anyofs})


typeToConstructor = {
    "string": JSONTypeString,
    "integer": JSONTypeInteger,
    "number": JSONTypeNumber,
    "boolean": JSONTypeBoolean,
    "null": JSONTypeNull,
    "array": JSONTypeArray,
    "object": JSONTypeObject
}

negTypeToConstructor = {
    "string": JSONTypeString.negString,
    "integer": JSONTypeInteger.negInteger,
    "number": JSONTypeNumber.negNumber,
    "boolean": JSONTypeBoolean.negBoolean,
    "null": JSONTypeNull.negNull,
    "array": JSONTypeArray.negArray,
    "object": JSONTypeObject.negObject
}

boolToConstructor = {
    "anyOf": JSONanyOf,
    "allOf": JSONallOfFactory,
    # "oneOf": JSONoneOf,
    # "not": JSONnotFactory
}

negBoolToConstructor = {
    # "anyOf": None,
    # "allOf": None,
    # "oneOf": None,
    # "not": None
}
