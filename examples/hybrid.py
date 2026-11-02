"""最小可运行混用示例；真实调用前配置 .env 和外部项目路径。"""
from atlas import AtlasConfig
from atlas.session import BrowserSession

config = AtlasConfig.from_root(env="sit", mode="run")
with BrowserSession(config) as session:
    actor = session.actor("tester")
    # 已登录后同步 Cookie；此处不隐式登录或创建生产数据。
    # actor.bake_session()
    # customer = actor.api.post("/web/legacy-web-mdm/orgCustomerInfo/findEntireCustDetails",
    #                           body={"customerCode": "...", "divisionCode": "..."})
    # assert customer.ok
    print("Actor 同时可具备 BrowseTheWeb 与 CallApi；请接入业务插件执行真实流程。")
