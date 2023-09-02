# Copyright 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# Modified from cmsis.py.

"""
CMSIS

The ARM Cortex Microcontroller Software Interface Standard (CMSIS) is a
vendor-independent hardware abstraction layer for the Cortex-M processor
series and specifies debugger interfaces. The CMSIS enables consistent and
simple software interfaces to the processor for interface peripherals,
real-time operating systems, and middleware. It simplifies software
re-use, reducing the learning curve for new microcontroller developers
and cutting the time-to-market for devices.

http://www.arm.com/products/processors/cortex-m/cortex-microcontroller-software-interface-standard.php


board 文件中增加了几个配置项：
- device_pack: 器件包文件夹名，未指定则用product_line 拼接：f'device_pack_{product_line}'
- device_include: 器件包中要引入的头文件文件夹，使用基于器件包的相对路径
- build.startup_file: startup 文件的文件名，未指定则用product_line 拼接：f{startup_{product_line}.s}
- build.system_file: system s文件的文件名，未指定则用product_line 拼接：f{system_{product_line}.s}
- build.use_device_pack_startup: 允许在device_pack 中查找startup 文件（true）
- build.use_device_pack_system: 允许在device_pack 中查找system 文件（true）
- debug.svd_name: svd 文件的文件名，未指定则用product_line 拼接：f{{product_line}.svd}



board 文件中废弃的配置项：
- svd_path
- frameworks: 不支持任何框架，如果需要，只能将其作为库手动引入
- core
- variant

device pack 的文件结构：

"""

import glob
import os
import string
import sys

from pathlib import Path
from itertools import chain
from typing import List, Optional, Iterable

from platformio.project.helpers import get_project_dir, get_project_all_lib_dirs

from SCons.Script import DefaultEnvironment

env = DefaultEnvironment()

board = env.BoardConfig()
mcu: str = board.get("build.mcu", "")
product_line: str = board.get("build.product_line", "")
assert product_line, "Missing MCU or Product Line field"

env.SConscript("_bare.py")

# config = ProjectConfig.get_instance()
project_path = Path(get_project_dir())
project_misc = project_path / 'misc'
project_src = project_path / 'src'
lib_path_list = [Path(p) for p in get_project_all_lib_dirs()]


# CMSIS_DIR = platform.get_package_dir("framework-cmsis")
# CMSIS_DEVICE_DIR = platform.get_package_dir("framework-cmsis-" + mcu[0:7])
# LDSCRIPTS_DIR = platform.get_package_dir("tool-ldscripts-ststm32")
# assert all(os.path.isdir(d) for d in (CMSIS_DIR, CMSIS_DEVICE_DIR, LDSCRIPTS_DIR))


# delete frameworks in board config
def do_not_support_any_framework():
    board.update('frameworks', [])


def two_layer_glob(folder: Path, name: str) -> Iterable[Path]:
    return chain(folder.glob(name), folder.glob(f'*/{name}'))


def find_device_pack_path() -> Optional[Path]:
    '''
    查找器件库，就是MDK 的器件库DFP。解压DFP 后直接用作一个库，方便扩展。
    器件库必须以device_pack 作为前缀，后面跟着产品线代码，比如HK32F030M 单片机，
    器件库的名称必须是device_pack_hk32f030mxx
    还可以在board 文件中指定为device_pack 参数
    查找器件库时，优先按device_pack 参数查找，其次按产品线。

    器件库可以放在两个位置，.pio/lib_deps 或lib，同一个器件库不能同时放在两个位置
    '''
    board_pack_name = board.get('device_pack', None)
    product_line_pack_name = f'device_path_{product_line}'

    find_pack = lambda name: chain(*[p.glob(name) for p in lib_path_list])

    if board_pack_name is not None:
        board_pack_name_list = list(find_pack(board_pack_name))
        list_len = len(board_pack_name_list)
        assert list_len <= 0, f'Found more than one device pack [ {board_pack_name} ].'

        if len(board_pack_name_list) == 1:
            return board_pack_name_list[0]

    product_line_name_pack_list = list(find_pack(product_line_pack_name))
    list_len = len(product_line_name_pack_list)
    assert list_len < 2, f'Found more than one device pack [ {product_line_pack_name} ].'
    if list_len == 0:
        print(f'?-> Device pack [ {product_line_pack_name} ] not found.')
        return None
    else:
        return product_line_name_pack_list[0]


def find_header_path_in_device_pack(pack_dir: Path) -> List[Path]:
    '''
    器件库中包含器件的寄存器头文件，但位置并不统一，所以要在board 文件中用"device_include": [] 指定。
    器件库中可能包含CMSIS 头文件，也可以用这种方式添加，但是HAL 或LL 库文件不行。
    简单起见，除了启动文件和system_xxx.c，器件库只会引入头文件，不包括源文件，所以不能把包含源文件的库放在这里。
    '''
    include_list = board.get("device_include", {})
    if len(include_list) == 0:
        return []

    include_path_list = [pack_dir / p for p in include_list]
    return include_path_list


def find_svd_file_path(pack_dir: Optional[Path]) -> Optional[Path]:
    '''
    优先在misc 文件夹中寻找符合要求的svd 文件，没找到则继续在器件库中搜索。
    svd 文件名在board 文件中定义，推荐定为debug.svd_name，
    也可以定义为debug.svd_path。

    找到svd 文件的路径后，board 文件设置的svd_path 将被覆盖，不更改源json 文件
    '''
    svd_name = board.get("debug.svd_name", None)
    if svd_name is None:
        svd_name = board.get("debug.svd_path", None)
        if svd_name is None:
            svd_name = f'{product_line}.svd'

    svd_in_misc: Path = project_misc / svd_name
    if svd_in_misc.exists():
        return svd_in_misc

    if pack_dir is None:
        return None
    all_svd = two_layer_glob(pack_dir, 'SVD/*.svd')
    target_svd = list(filter(lambda f: f.name == svd_name, all_svd))
    if len(target_svd) == 1:
        return target_svd[0]
    else:
        return None


def find_ldscript_file_path(pack_dir: Optional[Path]) -> Optional[Path]:
    '''
    优先在misc 文件夹中寻找符合要求的ld 文件，没找到则继续在器件库中搜索。
    svd 文件名在board 文件中定义，推荐定为build.ldscript，
    build.ldscript 不存在，则用mcu 拼接文件名

    器件库中的ld 文件必须放在Ldscript 文件夹下

    找到svd 文件的路径后，board 文件设置的svd_path 将被覆盖，不更改源json 文件
    '''
    ld_name = board.get("build.ldscript", None)
    if ld_name is None:
        ld_name = f'{product_line}.ld'

    ld_in_misc: Path = project_misc / ld_name
    if ld_in_misc.exists():
        return ld_in_misc

    if pack_dir is None:
        return None
    all_ld = two_layer_glob(pack_dir, 'Ldscript/*.ld')
    target_ld = list(filter(lambda f: f.name == ld_name, all_ld))
    if len(target_ld) == 1:
        return target_ld[0]
    else:
        return None


def find_source_file_in_device_pack(pack_dir: Path, parent: str, file_name: str) -> Optional[Path]:
    '''
    除非board 里定义了build.use_device_pack_startup = true，否则默认不从器件包里引入启动文件。
    启动文件必须以startup 开头，后缀为.s。优先检查src 文件夹中是否存在，不存在时再到器件库中查找。
    board 文件中可用build.startup_file 指定启动文件名，未指定，则用产品线拼接默认文件名

    器件库中的启动文件必须放在Startup 文件夹下
    '''
    result = list(two_layer_glob(pack_dir, f'{parent}/{file_name}'))
    result_len = len(result)
    if result_len == 0:
        return None
    elif result_len == 1:
        print(f'--> Found source file {file_name} in device pack.')
        return result[0]
    else:
        sys.stderr.write(f"!-> Found multiple file {file_name} in device pack. Discard.")
        return None


def find_source_file_in_src(file_name: str) -> bool:
    '''
    不检查src 文件夹下是否存在重复的启动文件，src 下的源文件默认都会编译，可能存在子文件夹，
    就算文件名有重复的，也可能是不同的文件，只能假设是用户有意为之
    '''
    result = list(two_layer_glob(project_src, file_name))
    result_len = len(result)
    if result_len > 0:
        return True
    else:
        print(f'--> No file {file_name} in src.')
        return False


def get_startup_file_name() -> str:
    file_name = f'startup_{product_line}.[sS]'
    file_name: str = board.get("build.startup_file", file_name)
    return file_name


def get_system_file_name() -> str:
    file_name = f'system_{product_line}.c'
    file_name: str = board.get("build.system_file", file_name)
    return file_name


do_not_support_any_framework()

env.VerboseAction(f'--> Finding device pack in: {lib_path_list}')
pack = find_device_pack_path()
if pack is not None:
    env.VerboseAction(f'--> Device pack selected: {pack}')


# 如果找不到LDSCRIPT，就无法完成编译

ld_path = find_ldscript_file_path(pack)
if ld_path is None:
    sys.stderr.write(f"!-> Ldscript file not found.")
    raise ValueError('No ldscript file or wrong naming.')
else:
    env.Replace(LDSCRIPT_PATH=str(ld_path.resolve()))


# 覆盖board 文件的svd_path 参数

svd_path = find_svd_file_path(pack)
if svd_path is None:
    sys.stderr.write(f"!-> SVD file not found. Debug feature may not work.")
else:
    board.update('debug.svd_path', str(svd_path.resolve()))


# 按需引入device_pack 中的头文件路径

if pack is not None:
    header_path_list = find_header_path_in_device_pack(pack)
    if len(header_path_list) > 0:
        env.Append(CPPPATH=header_path_list)


# The final firmware is linked against standard library with two specifications:
# nano.specs - link against a reduced-size variant of libc
# nosys.specs - link against stubbed standard syscalls

env.Append(LINKFLAGS=["--specs=nano.specs", "--specs=nosys.specs"])


# 查找startup 文件

startup_name = get_startup_file_name()
is_startup_in_src = find_source_file_in_src(startup_name)
startup_path = None
if not is_startup_in_src:
    if board.get('build.use_device_pack_startup', False) and pack is not None:
        startup_path = find_source_file_in_device_pack(pack, 'Startup', startup_name)
    else:
        sys.stderr.write(f"!-> Startup file not found. Ignore this if it's ok.")

# 查找system 文件

system_name = get_system_file_name()
is_system_in_src = find_source_file_in_src(system_name)
system_path = None
if not is_system_in_src:
    if board.get('build.use_device_pack_system', False) and pack is not None:
        system_path = find_source_file_in_device_pack(pack, 'SystemSource', system_name)
    else:
        sys.stderr.write(f"!-> System file not found. Ignore this if it's ok.")


# 编译device_pack 中的startup 文件


def get_relative_path_to_device_pack(file_path: Path) -> str:
    assert pack is not None
    return file_path.resolve().as_posix().removeprefix(f'{pack.resolve().as_posix()}/')


def build_source_file_in_device_pack(file_path: Path):
    assert pack is not None
    relative_path: str = get_relative_path_to_device_pack(file_path)

    env.BuildSources(
        os.path.join("$BUILD_DIR", "SourceInDevicePack"),
        pack.resolve().as_posix(),
        src_filter=[
            "-<*>",
            f"+<{relative_path}>",
        ],
    )


if startup_path is not None:
    build_source_file_in_device_pack(startup_path)

# 编译device_pack 中的system 文件

if system_path is not None:
    build_source_file_in_device_pack(system_path)
