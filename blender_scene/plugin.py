"""SystemPromptPlugin — Python translation of system_prompt.rs.

A Plugin that sets the system prompt for a specific workflow step.
Creates a before_run hook returning {"system_prompt": prompt, "additional_messages": []},
wrapped in a plugin via lh.create_plugin(name="system-prompt", hooks=[hook]).

为特定工作流步骤设置系统提示词的插件。
system_prompt.rs 的 Python 翻译。
"""
import llm_harness_py as lh


def create_system_prompt_plugin(prompt: str):
    """Create a system-prompt plugin that sets the system prompt before each run.

    创建一个 system-prompt 插件，在每次运行前设置系统提示词。
    """
    def before_run_cb(ctx: dict):
        """Return the system prompt for this step.

        返回该步骤的系统提示词。
        """
        return {
            "system_prompt": prompt,
            "additional_messages": [],
        }

    hook = lh.create_before_run_hook(before_run_cb)
    return lh.create_plugin(name="system-prompt", hooks=[hook])
