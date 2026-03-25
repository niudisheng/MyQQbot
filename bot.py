import nonebot
from dotenv import load_dotenv
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

load_dotenv()

nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

# 插件目录、built-in 等由 pyproject.toml [tool.nonebot] 统一加载
nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
