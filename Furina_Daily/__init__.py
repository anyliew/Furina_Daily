"""Furina_Daily 内层包入口：声明插件单例。

业务子模块放在本包下的子目录（utils / config / fetchers / render / commands /
push / help），框架嵌套加载会自动遍历导入，触发各 SV 的命令注册与钩子。
"""

from gsuid_core.sv import Plugins

# 命令自带「日报」字样（如「日报」「芙芙日报」「日报压缩」），
# 不存在统一前缀，因此不设 force_prefix，允许空前缀直接按字面命令匹配。
Plugins(
    name="Furina_Daily",
    allow_empty_prefix=True,
    alias=["furina_daily", "furina-daily", "芙芙日报"],
)
