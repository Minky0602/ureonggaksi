import multiprocessing

from ureonggaksi.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # PyInstaller: spawn 작업자가 앱을 다시 띄우지 않게
    raise SystemExit(main())
