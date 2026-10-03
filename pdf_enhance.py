"""PDF-Enhance 命令行入口。"""

from pdf_enhance_core.cli import APP_NAME, APP_NAME_ZH, APP_VERSION, console, run_wizard

__all__ = ["APP_NAME", "APP_NAME_ZH", "APP_VERSION", "run_wizard"]


if __name__ == "__main__":
    try:
        run_wizard()
    except KeyboardInterrupt:
        console.print("\n[dim]用户中断操作，已退出。[/dim]")
    except Exception as exc:
        console.print(f"\n[bold red]发生未知错误：{exc}[/bold red]")
