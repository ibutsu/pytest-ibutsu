from typing import cast
from typing import List
from typing import MutableMapping
from typing import Union

import _pytest.nodes  # hack for types


def safe_string(o):
    """This will make string out of ANYTHING without having to worry about the stupid Unicode errors

    This function tries to make str/unicode out of ``o`` unless it already is one of those and then
    it processes it so in the end there is a harmless ascii string.

    Args:
        o: Anything.
    """
    if not isinstance(o, str):
        o = str(o)
    if isinstance(o, bytes):
        o = o.decode("utf-8", "ignore")
    o = o.encode("ascii", "xmlcharrefreplace").decode("ascii")
    return o


def merge_dicts(old_dict, new_dict):
    for key, value in old_dict.items():
        if key not in new_dict:
            new_dict[key] = value
        elif isinstance(value, dict):
            merge_dicts(value, new_dict[key])


class DATA_OPTIONS(MutableMapping[str, Union["DATA_OPTIONS", str]]):
    @staticmethod
    def new():
        return cast(DATA_OPTIONS, {})


def parse_data_option(data_list: List[str]) -> DATA_OPTIONS:
    data_dict: DATA_OPTIONS = DATA_OPTIONS.new()

    for data_str in data_list:
        if not data_str:
            continue
        key_str, value = data_str.split("=", 1)
        (*keys, item) = key_str.split(".")
        current_item: DATA_OPTIONS = data_dict
        for key in keys:
            if key not in current_item:
                new = current_item[key] = DATA_OPTIONS.new()
                current_item = new
            else:
                current_item = cast(DATA_OPTIONS, current_item[key])

        current_item[item] = value
    return data_dict


def get_test_idents(item: _pytest.nodes.Item):
    try:
        return item.location[2], item.location[0]
    except AttributeError:
        try:
            return item.fspath.strpath, None  # type: ignore
        except AttributeError:
            return (None, None)


def get_name(obj):
    return getattr(obj, "_param_name", None) or getattr(obj, "name", None) or str(obj)
