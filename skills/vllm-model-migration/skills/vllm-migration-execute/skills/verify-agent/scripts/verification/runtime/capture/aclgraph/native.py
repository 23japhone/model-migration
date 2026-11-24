"""读取图 dumper 的普通 Tensor ZIP 元数据；控制端无需加载 PyTorch 或权重。"""
from collections import OrderedDict
from io import BytesIO
from math import prod
import pickle
import re
import zipfile

STORAGE = {'BFloat16': ('bfloat16', 2), 'Half': ('float16', 2), 'Float': ('float32', 4),
           'Double': ('float64', 8), 'Long': ('int64', 8), 'Int': ('int32', 4),
           'Short': ('int16', 2), 'Byte': ('uint8', 1), 'Char': ('int8', 1), 'Bool': ('bool', 1)}


def call_info(filename):
    match = re.fullmatch(r'(.+?)\.(\d+)\.(input_kwargs|input|output)(?:\.(.+))?\.pt', filename)
    if match is None:
        return {'call': None, 'occurrence': None, 'io': None}
    name, occurrence, io, argument = match.groups()
    return {'call': name, 'occurrence': int(occurrence), 'io': io, 'argument': argument}


def rebuild(storage, offset, shape, strides, *unused):
    dtype, width, name, count = storage
    if offset != 0 or any(type(n) != int or n < 0 for n in shape) or prod(shape) != count:
        raise ValueError('图原件不是完整的致密 Tensor')
    return {'dtype': 'torch.'+dtype, 'shape': list(shape), 'stride': list(strides),
            'storage': name, 'data_bytes': count*width}


class TensorMetadata(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) == ('torch._utils', '_rebuild_tensor_v2'):
            return rebuild
        if (module, name) == ('collections', 'OrderedDict'):
            return OrderedDict
        if module == 'torch' and name.endswith('Storage') and name[:-7] in STORAGE:
            return STORAGE[name[:-7]]
        raise ValueError('未知的 Tensor 元数据类型')

    def persistent_load(self, value):
        kind, (dtype, width), storage, device, count = value
        if kind != 'storage' or not storage.isdecimal() or device != 'cpu' or type(count) != int:
            raise ValueError('无效图原件存储')
        return dtype, width, storage, count


def tensor_header(path):
    try:
        with zipfile.ZipFile(path) as archive:
            header, = [entry for entry in archive.infolist() if entry.filename.endswith('/data.pkl')]
            if header.file_size > 65536:
                raise ValueError('图原件元数据过大')
            meta = TensorMetadata(BytesIO(archive.read(header))).load()
            if not isinstance(meta, dict) or not isinstance(meta.get('storage'), str):
                raise ValueError('图原件缺少单一 Tensor 存储')
            storage = header.filename[:-8]+'data/'+meta.pop('storage')
            if archive.getinfo(storage).file_size != meta['data_bytes']:
                raise ValueError('图原件存储字节数不符')
            return meta
    except (EOFError, KeyError, TypeError, pickle.UnpicklingError, zipfile.BadZipFile) as exc:
        raise ValueError('无法解析图原生 Tensor') from exc
