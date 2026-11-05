"""源码来源类型：实际安装包与 Git 基线分开记录。"""
import re


def validate(item):
    kind = item.get('kind', 'git')
    if kind == 'git':
        if not isinstance(item.get('revision'), str) or not re.fullmatch('[0-9a-f]{40}', item['revision']):
            raise ValueError('Git 源码 revision 必须为完整的小写 commit OID')
        if {'distribution', 'version', 'image_id'} & item.keys():
            raise ValueError('Git 来源不能混入安装包身份')
    elif kind == 'installed_package':
        if 'revision' in item:
            raise ValueError('安装包快照不能声明未证明的 Git revision')
        for key in ('distribution', 'version'):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError('安装包快照须固定 distribution 与 version')
        if not re.fullmatch('sha256:[0-9a-f]{64}', str(item.get('image_id', ''))):
            raise ValueError('安装包快照须绑定实际镜像身份')
        if not re.fullmatch('[0-9a-f]{64}', str(item.get('expected_identity', ''))):
            raise ValueError('安装包快照须固定实际 Python 文件集合摘要')
    else:
        raise ValueError('未知源码来源 kind')
    return item


def projection(source):
    if source.get('kind', 'git') == 'git':
        return {'declared_revision': source['revision'], 'revision_verification': 'not_evaluated'}
    return {'kind': 'installed_package', 'declared_revision': None, 'revision_verification': 'not_applicable',
            'distribution': source['distribution'], 'version': source['version'], 'image_id': source['image_id']}
