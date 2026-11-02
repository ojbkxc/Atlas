"""无认证公共页面冒烟：验证 Playwright 执行底座，不访问公司业务系统。"""
from atlas.config import AtlasConfig
from atlas.session import BrowserSession

config = AtlasConfig.from_root(env="sit", mode="run")
with BrowserSession(config) as session:
    session.page.set_content("<title>Atlas smoke</title><button id='ok'>OK</button>")
    session.page.locator("#ok").click()
    assert session.page.title() == "Atlas smoke"
print("Playwright local page: PASSED")
