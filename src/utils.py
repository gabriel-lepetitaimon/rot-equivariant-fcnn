from collections import OrderedDict


def is_dict(o):
    return isinstance(o, (dict, AttributeDict))


def recursive_dict_update(destination, origin, append=False):
    for n, v in origin.items():
        dest_v = destination.get(n, None)
        if is_dict(v) and is_dict(dest_v):
            recursive_dict_update(destination[n], v)
        elif append and isinstance(v, list) and isinstance(dest_v, list):
            for list_v in v:
                append_needed = True
                if is_dict(list_v) and isinstance(append, str) and append in list_v:
                    key = list_v[append]
                    for i in range(len(dest_v)):
                        if is_dict(dest_v[i]) and dest_v[i] and dest_v[i].get(append, None) == key:
                            recursive_dict_update(dest_v[i], list_v, append=append)
                            append_needed = False
                if append_needed:
                    dest_v.append(list_v)
        else:
            destination[n] = v


def recursive_dict_map(dictionnary, function):
    r = {}
    for n, v in dictionnary.items():
        if is_dict(v):
            v = recursive_dict_map(v, function=function)
        else:
            v = function(n, v)
        if v is not None:
            r[n] = v
    return r


class AttributeDict(OrderedDict):
    @staticmethod
    def from_dict(d, recursive=False):
        r = AttributeDict()
        for k, v in d.items():
            if is_dict(v) and recursive:
                v = AttributeDict.from_dict(v, True)
            r[k] = v
        return r

    @staticmethod
    def from_json(json):
        from json import loads
        d = loads(json)
        return AttributeDict.from_dict(d, recursive=True)

    @staticmethod
    def from_yaml(yaml_doc):
        import yaml
        d = yaml.load(yaml_doc, Loader=yaml.FullLoader)
        return AttributeDict.from_dict(d, recursive=True)

    def to_dict(self):
        return recursive_dict_map(self, lambda k, v: v)

    def to_json(self):
        from json import dumps
        return dumps(self)

    def to_yaml(self, file=None):
        import yaml
        return yaml.dump(self.to_dict(), stream=file, default_flow_style=False)

    def __setitem__(self, key, value):
        if not isinstance(key, str) or '.' in key:
            raise ValueError('Invalid AttributeDict key: %s.' % repr(key))
        super(AttributeDict, self).__setitem__(key, value)

    def __getitem__(self, item):
        if isinstance(item, int):
            k = list(self.keys())
            if item > len(k):
                raise IndexError('Index %i out of range (AttributeDict length: %s)' % (item, len(k)))
            return super(AttributeDict, self).__getitem__(list(self.keys())[item])
        elif isinstance(item, str):
            return super(AttributeDict, self).__getitem__(item)
        else:
            return super(AttributeDict, self).__getitem__(str(item))

    def __getattr__(self, item):
        if item in self:
            return self[item]
        raise AttributeError('%s is unknown' % item)

    def __iter__(self):
        for v in self.values():
            yield v

    def __len__(self):
        return len(self.keys())

    def recursive_update(self, d, **kwargs):
        recursive_dict_update(self, d)
        for k, v in kwargs.items():
            recursive_dict_update(self[k], v)
        return self

    def filter(self, condition, recursive=False):
        for k, v in self.items():
            if recursive and isinstance(v, AttributeDict):
                v.filter(condition, True)
            elif not condition(k,v):
                del self[k]
        return self

    def map(self, f, recursive=False, remove_if_none=False):
        for k, v in self.items():
            if recursive and isinstance(v, AttributeDict):
                v.map(f, True)
            else:
                r = f(k, v)
                if remove_if_none and r is None:
                    del self[k]
                else:
                    self[k] = r
        return self

    def copy(self):
        from copy import deepcopy
        return deepcopy(self)


# -----------------------------------------------------------
# -                     LUT COLOR MAP                       =
# -----------------------------------------------------------
_last_map = None
_last_lut = None

def if_none(v,o):
    return o if v is None else v

def prepare_lut(map, source_dtype=None, axis=None, sampling=None, default=None, keep_dims=True):
    assert isinstance(map, dict) and len(map)

    import numpy as np

    # Prepare map
    source_list = []
    dest_list = []
    source_shape = None
    dest_shape = None

    add_empty_axis = False
    for source, dest in map.items():
        if isinstance(source, str):
            source = str2color(source, uint8=str(source_dtype) == 'uint8')
        source = np.array(source)
        if source.ndim == 0:
            source = source.reshape((1,))
            add_empty_axis = True
        if source_shape is None:
            source_shape = source.shape
        elif source_shape != source.shape:
            raise ValueError('Invalid source values: %s (shape should be %s)' % (repr(source), source_shape))
        source_list.append(source)

        if isinstance(dest, str):
            dest = str2color(dest, uint8=str(source_dtype) == 'uint8')
        dest = np.array(dest)
        if dest.ndim == 0:
            dest = dest.reshape((1,))
        if dest_shape is None:
            dest_shape = dest.shape
        elif dest_shape != dest.shape:
            raise ValueError('Invalid destination values: %s (shape should be %s)' % (repr(source), dest_shape))
        dest_list.append(dest)

    if axis:
        if isinstance(axis, int):
            axis = np.array([axis])
        elif isinstance(axis, (list, tuple, np.ndarray)):
            axis = np.array(axis)
        else:
            raise ValueError('Invalid axis parameter: %s (should be one or a list of axis)' % repr(axis))
    elif axis is None:
        axis = np.arange(len(source_shape))

    # Read shape
    n_axis = len(axis)
    source_size = int(np.prod(source_shape))
    dest_size = int(np.prod(dest_shape))
    dest_axis = sorted(axis)[0]

    # Prepare lut table
    sources = []
    lut_dests = [if_none(default, np.zeros_like(dest))]
    for s, d in zip(source_list, dest_list):
        source = np.array(s).flatten()
        dest = np.array(d)
        if dest.shape:
            dest = dest.flatten()
        sources.append(source)
        lut_dests.append(dest)

    sources = np.array(sources).astype(dtype=source_dtype)
    lut_dests = np.array(lut_dests)

    mins = sources.min(axis=0)
    maxs = sources.max(axis=0)

    if sampling is None:
        if 'float' in str(sources.dtype) and mins.min() >= 0 and maxs.max() <= 1:
            sampling = 1 / 255
    elif sampling == 'gcd':
        sampling = np.zeros(sources.shape[1:], dtype=np.float)
        for i in range(sources.shape[0]):
            sampling[i] = 1 / np.gcd.reduce(sources[i]) / 2
    if not sampling:
        sampling = 1

    if not isinstance(sampling, str):
        sources = (sources / sampling).astype(np.int32)
        mins = sources.min(axis=0)
        maxs = sources.max(axis=0)
        stride = np.cumprod([1] + list((maxs - mins + 1)[1:][::-1]), dtype=np.uint32)[::-1]

        flatten_sources = np.sum((sources-mins) * stride, dtype=np.uint32, axis=1)
        id_sorted = flatten_sources.argsort()
        flatten_sources = flatten_sources[id_sorted]
        lut_dests[1:] = lut_dests[1:][id_sorted]

        if np.all(flatten_sources == np.arange(len(flatten_sources))):
            lut_sources = None
        else:
            lut_sources = np.zeros((int(np.prod(maxs - mins + 1)),), dtype=np.uint32)
            for s_id, s in enumerate(flatten_sources):
                lut_sources[s] = s_id + 1
    else:
        lut_sources = 'nearest'
        stride = 1
        mins = 0

    def f_lut(array):
        if len(axis) > 1 and axis != np.arange(len(axis)):
            array = np.moveaxis(array, source=axis, destination=np.arange(len(axis)))
        elif add_empty_axis:
            array = array.reshape((1,) + array.shape)

        # if 'int' not in str(array.dtype):
        #     log.warn('Array passed to apply_lut was converted to int32. Numeric precision may have been lost.')

        # Read array shape
        a_source_shape = array.shape[:n_axis]
        map_shape = array.shape[n_axis:]
        map_size = int(np.prod(map_shape))

        # Check source shape
        if a_source_shape != source_shape:
            raise ValueError('Invalid dimensions on axis: %s. (expected: %s, received: %s)'
                             % (str(axis), str(source_shape), str(a_source_shape)))

        # Prepare table
        array = np.moveaxis(array.reshape(source_shape + (map_size,)), -1, 0).reshape((map_size, source_size))

        if isinstance(sampling, str):
            id_mapped = None
        else:
            if sampling != 1:
                array = (array / sampling).astype(np.int32)
            id_mapped = np.logical_not(np.any(np.logical_or(array > maxs, array < mins), axis=1))
            array = np.sum((array - mins) * stride, axis=1).astype(np.uint32)

        # Map values
        if isinstance(lut_sources, str): # and lut_sources == 'nearest':
            a = np.sum((array[np.newaxis, :, :]-sources[:, np.newaxis, :])**2, axis=-1)
            a = np.argmin(a, axis=0)+1
        elif lut_sources is not None:
            a = np.zeros(shape=(map_size,), dtype=np.uint32)
            if lut_sources is not None:
                a[id_mapped] = lut_sources[array[id_mapped]]
            else:
                a[id_mapped] = array[id_mapped]+1
        else:
            if lut_sources is not None:
                a = lut_sources[array]
            else:
                a = array+1
        array = lut_dests[a]

        del a
        del id_mapped

        # Reshape
        array = array.reshape(map_shape + dest_shape)

        array = np.moveaxis(array, np.arange(len(map_shape), array.ndim),
                            np.arange(dest_axis, dest_axis + len(dest_shape)) if len(dest_shape) != len(axis) else axis)
        if not keep_dims and dest_shape == (1,):
            array = array.reshape(map_shape)

        return array

    f_lut.sources = sources
    f_lut.lut_sources = lut_sources
    if isinstance(lut_sources, np.ndarray):
        f_lut.mins = mins
        f_lut.maxs = maxs
        f_lut.stride = stride
    f_lut.lut_dests = lut_dests
    f_lut.sampling = sampling
    f_lut.source_dtype = source_dtype
    f_lut.keep_dims = keep_dims
    return f_lut


def apply_lut(array, map, axis=None, sampling=None, default=None):
    # import numpy as np
    #
    # a = array
    # if axis:
    #     if isinstance(axis, int):
    #         axis = np.array([axis])
    #     elif isinstance(axis, (list, tuple, np.ndarray)):
    #         axis = np.array(axis)
    #         a = np.moveaxis(a, source=axis, destination=np.arange(len(axis)))
    #     else:
    #         raise ValueError('Invalid axis parameter: %s (should be one or a list of axis)' % repr(axis))
    # elif axis is None:
    #     axis = np.arange(np.array(next(iter(map.keys()))).ndim)
    #     if len(axis) == 0:
    #         axis = None
    #         a = array.reshape((1,) + a.shape)
    #
    # n_axis = len(axis) if axis else 1
    # source_shape = a.shape[:n_axis]
    # source_size = int(np.prod(source_shape))
    # map_shape = a.shape[n_axis:]
    # map_size = int(np.prod(map_shape))
    #
    # a = a.reshape((source_size, map_size))
    # mins = a.min(axis=-1)
    # maxs = a.max(axis=-1)
    # a_minmax = (mins, maxs)

    f_lut = prepare_lut(map, source_dtype=array.dtype, axis=axis, sampling=sampling, default=default)
    return f_lut(array)


def str2color(str_color, bgr=True, uint8=True):
    import numpy as np
    if not str_color or not isinstance(str_color, str):
        return np.zeros((3,), dtype=np.uint8 if uint8 else np.float16)

    c = str_color.split('.')
    if len(c) == 1:
        c_str = c[0]
        m = 1
    else:
        c_str = c[0]
        m = float('0.'+c[1])
        if c_str.lower() == 'black':
            m = 1-m

    try:
        c = dict(
            blue=(0,0,255),
            magenta=(255,0,255),
            red=(255,0,0),
            yellow=(255,255,0),
            green=(0,255,0),
            cyan=(0,255,255),
            turquoise=(0,255,127),
            sky_blue=(0,127,255),
            orange=(255,127,0),
            apple_green=(127,255,0),
            pruple=(127,0,255),
            pink=(255,0,127),
            white=(255,255,255),
            grey=(127,127,127),
            black=(0,0,0)
        )[c_str.lower()]
    except KeyError:
        raise ValueError('Invalid color code: %s' % c) from None
    c = np.array(c, dtype=np.float16)*m
    if uint8:
        c = c.astype(dtype=np.uint8)
    else:
        c /= 255
    if bgr:
        c = c[::-1]
    return c