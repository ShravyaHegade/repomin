import os
from pathlib import Path
import resource
import signal


resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
selected = signal.SIGABRT if Path("required.txt").exists() else signal.SIGTERM
os.kill(os.getpid(), selected)
