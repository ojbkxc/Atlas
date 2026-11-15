"""pf-3 真机取证：本地 set_content 页面（零凭据），Chrome channel，验证语义等待
「失败点不移动」。跑三个场景：
  1. mask_gone：页面先显示 loading mask 800ms 后消失 → wait(target=mask_gone) 应在
     mask 消失后返回（耗时约 800ms，不是固定 1000ms）；
  2. 固定睡回落（无 target）：旧行为=固定 ms；
  3. 失败点不移动：mask 永不消失 → 语义等待超时抛 TimeoutError（不洗绿）。
"""
import time
from playwright.sync_api import sync_playwright
from atlas import autopilot
from atlas.autopilot import ExploreContext

PAGE_MASKED = """<!doctype html><html><body>
<div id="app" class="el-loading-parent" style="height:300px;">
  <table class="el-table"><tbody><tr><td>行1</td></tr></tbody></table>
</div>
<script>
// mask 由外层脚本延迟注入（set_content 返回后才起 800ms 计时），避免 load 事件竞态
</script></body></html>"""

PAGE_NEVER_SETTLES = """<!doctype html><html><body>
<div class="el-loading-parent" style="height:300px;"></div>
<script>
// element-ui 族特征：mask 缺席时 _settle_mask 会查族前缀判「未加载 vs 非本族」——
// 本页 mask 由外层注入（永远在场），族前缀检查不会触发；但保留 el- 类名防误判。
</script></body></html>"""

INJECT_MASK_JS = """() => {
  const m = document.createElement('div');
  m.className = 'el-loading-mask';
  // 显式定尺寸：父容器无定位时 absolute mask 会 height=0 → Playwright 判 hidden，
  // wait_for_selector(state=hidden) 立即返回假绿。fixed + inset:0 保证真可见。
  m.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;display:flex;';
  document.querySelector('.el-loading-parent').appendChild(m);
}"""

HIDE_MASK_AFTER_JS = """(ms) => setTimeout(function(){
  var m = document.querySelector('.el-loading-mask');
  if (m) m.style.display = 'none';
}, ms)"""


def run(page, html, action, ctx, *, inject=True, hide_after_ms=800):
    page.set_content(html, wait_until="load")
    if inject:
        page.evaluate(INJECT_MASK_JS)
        page.evaluate(HIDE_MASK_AFTER_JS, hide_after_ms)
    t0 = time.perf_counter()
    detail = autopilot._execute(page, action, ctx)
    return time.perf_counter() - t0, detail


def main():
    ctx = ExploreContext.__new__(ExploreContext)
    ctx.values = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()

        # 场景 1：mask 800ms 后消失 → 语义等待约 800ms 返回
        elapsed1, detail1 = run(page, PAGE_MASKED,
                                {"action": "wait", "target": "mask_gone",
                                 "timeout_ms": 5000}, ctx)
        print(f"[S1 mask_gone]   elapsed={elapsed1*1000:.0f}ms detail={detail1}")

        # 场景 2：无 target（旧行为）→ 固定 1000ms（不注入 mask，旧行为逐字节）
        elapsed2, detail2 = run(page, PAGE_MASKED, {"action": "wait"}, ctx,
                                inject=False)

        # 场景 3：mask 注入后永不消失 → 语义等待在 timeout_ms 内抛 TimeoutError（不洗绿）
        raised = False
        try:
            run(page, PAGE_NEVER_SETTLES,
                {"action": "wait", "target": "mask_gone", "timeout_ms": 600}, ctx,
                inject=True, hide_after_ms=10**9)   # 注入但永不隐藏
        except Exception as e:   # playwright.sync_api.TimeoutError 是独立类，不与内置 TimeoutError 同族
            raised = "Timeout" in type(e).__name__
        print(f"[S3 timeout]     raised={raised} (期望 True；mask 永不消失时不洗绿)")

        browser.close()

    # 断言（取证报告）
    assert 700 <= elapsed1*1000 < 2000, f"S1 应约 800ms 返回，实际 {elapsed1*1000:.0f}ms"
    assert detail1["target"] == "mask_gone"
    assert 950 <= elapsed2*1000 < 1500, f"S2 旧行为固定睡 1000ms，实际 {elapsed2*1000:.0f}ms"
    assert detail2 == {"action": "wait", "ms": 1000}
    assert raised, "S3 必须抛 TimeoutError（mask 永不消失不得洗绿）"
    print("\nREALMACHINE_PF3_PASSED")


if __name__ == "__main__":
    main()
