"""Windows 冻结版入口；必须先处理 multiprocessing 子进程参数。"""

from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()
    from pdf_enhance_gui import main

    main()
