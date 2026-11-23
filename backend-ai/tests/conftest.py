"""测试必须与开发机环境隔离。

背景（2026-11-23 发现）：本地 `.env` 配的是真实网关，于是 `pytest` 会真的调模型——
`test_plan` / `test_adjust` 里断言 `mock: true` 的两条用例直接失败（表现是「本地红、CI 绿」），
更糟的是**每次跑单测都在花钱**，只是没人注意。

单测的输入应该是代码，不是环境。这里在 `conftest` 里强制回到 mock：
优先级上环境变量高于 `config/config.yaml`，而在 conftest 里写入又早于任何测试模块导入 `app.main`，
所以无论 shell 里有没有 `.env`，测试看到都是 mock provider。

需要真实 provider 的测试（例如 provider 错误分型）应自己构造对象并显式传参，
不要指望环境变量——那是隐式依赖，换台机器就会变红。
"""

import os

os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1"
os.environ["LLM_MODEL"] = "mock"
