# -*- coding: utf-8 -*-
"""测试用的"假模型"（test doubles）—— 三个测试文件共用。

★ 为什么放在 conftest.py，而不是各测试文件里各写一份：
  `_FakeLLM` 原本在 test_guard.py 和 test_llm_resilience.py 里【各抄了一份】
  （commit ab9a71c 定性的正是"同一颗哑弹抄两份"这类问题）。
  test_eval_set.py 这次也要用 —— 再抄就是第三份。
  pytest 会先加载 conftest.py，各测试文件 `from conftest import ...` 即可拿到。

★ 为什么要有假货：真模型要联网、要 API key、每次结果还可能不一样。
  测试关心的是"流程对不对"（该拒答时拒答、该命中时命中），不是"模型说了什么"，
  所以把模型换成人偶，让流程能脱离网络被反复验证。

★ 用假模型【不等于】放弃验证真实调用：
  真的 `ChatDeepSeek(...)` 构造目前【没有任何测试覆盖】（构造就要 key），
  所以 service.py 里 `llm` 属性那几个 kwarg 写错了也没人会发现 —— 这是已知的账，
  靠真机跑 / 手工验，不靠这一个假模型来兜。
"""

import sys
import time
from pathlib import Path

# ★ 本文件除了"假模型"，还顺手做一件事：跑测试时提醒「本地钩子装没装」。
#   见文件末尾的 pytest_sessionstart —— 那里写了为什么放在这儿。


# ==================================================================
# 跑测试时提醒一句：本地那两道防线装了吗
# ==================================================================
# 为什么放在这里（2026-10-11）：
#   `scripts/hooks/README.md:160-163` 自己写着 —— 本地钩子（pre-commit / pre-push）
#   是【唯一真正会挡】的两道防线，而它们**不进仓库**，换 clone 就没了
#   ⇒ 别人（面试官也会 clone）clone 下来默认一道都没有。
#   而 README「30 秒跑起来」第 3 步就是跑 pytest ⇒ 这里是【人一定会看到】的地方。
#
# ★ 为什么只提醒、不让测试变红：
#   clone 下来看到 "60 passed" 变成 "59 passed, 1 failed"，只会让人以为这作品是坏的。
#   "提醒"是能看见，"失败"是看起来坏了 —— 两回事。
# ★ 判断逻辑【不在本文件里重写一遍】，而是从 scripts/install_hooks.py 复用
#   —— 本文件自己的 docstring 就写过教训：「同一颗哑弹抄两份」。
def pytest_sessionstart(session):
    scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
    try:
        sys.path.insert(0, scripts_dir)
        try:
            import install_hooks
        finally:
            # 只为这次导入加路径，不污染整个测试会话的 sys.path
            if scripts_dir in sys.path:
                sys.path.remove(scripts_dir)
        root = install_hooks.repo_root()
        ok, why = install_hooks.installed(root)
    except (SystemExit, Exception):
        # 不是 git 仓库（比如把 zip 下载下来解压）⇒ 根本不存在 git 钩子这回事，
        # 这段提醒不适用。★ 这不是"静默吞掉故障"：没有 git 就无所谓钩子。
        return
    if ok:
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    lines = [
        "",
        f"⚠️  本地钩子【没装】—— {why}",
        "    它们是这个仓库【唯一真正会挡】的两道防线（远端 CI 只会喊、不拦合并），",
        "    而 .git/hooks/ 不进仓库，所以新 clone 默认一道都没有。",
        "    装它（一条命令，幂等）：  python scripts/install_hooks.py",
        "",
    ]
    if tr is not None:
        for ln in lines:
            tr.write_line(ln)          # 走 terminalreporter：不受 pytest 捕获影响
    else:
        print("\n".join(lines))


class _FakeResp:
    """假返回：只需要有 .content 这个属性，和真模型返回的对象长得一样"""

    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """假模型：不管问什么都返回指定字符串"""

    def __init__(self, reply):
        self.reply = reply

    def invoke(self, messages):
        return _FakeResp(self.reply)


class _SlowLLM:
    """模拟"模型卡住了"：一直不返回。

    注意 sleep 只有 1 秒（不是 5 秒）—— 超时后那个线程其实还在后台跑完，
    Python 退出时会等它，所以故意设短一点，免得拖慢整个测试。
    """

    def invoke(self, messages):
        time.sleep(1)
        # 睡完还是返回一个值（原来就在 test_llm_resilience.py 里，搬过来保持行为一致）。
        # 实际测试中超时（0.05 秒）先触发，这一行不会被观察到 —— 留着是为了
        # 将来有人把超时调长之后，它不至于变成"永远不返回"的另一颗哑弹。
        return _FakeResp("{}")


class _UsageResp:
    """带 usage_metadata 的假返回。

    ★ 为什么 _FakeResp 不直接给它加 usage：
      usage 是【计量改动】才会去读的字段，而本文件绝大多数测试压根不关心计量。
      把它塞进共用的 _FakeResp，等于让所有测试都假装自己能报 token ——
      哪天真有 bug 让 usage 变成幻觉，这些测试也照样绿。
      ⇒ 用两个分开的假货：要测计量的用例显式换 _UsageLLM。
    """

    def __init__(self, content, inp=100, out=20, cache_read=0, total=None):
        self.content = content
        self.usage_metadata = {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": total if total is not None else inp + out,
            "input_token_details": {"cache_read": cache_read},
        }


class _UsageLLM:
    """每次调用都返回一个带 usage 的假返回，且【每次的 token 数不同】。

    ★ 每次不同是故意的：这样"只报最后一次"和"全部累加"两种实现测出来的
      结果不一样（3 次分别是 100/200/300 时，累加=600、只留最后=300），
      判据才真的能区分这两种实现 —— 如果每次都一样，
      覆盖写和累加写会得到同一个数，测了等于没测。
    """

    def __init__(self, reply="{}"):
        self.reply = reply
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        n = self.calls
        return _UsageResp(self.reply, inp=100 * n, out=10 * n)
