import sys
import signal
import time

from rejoin_core import SystemEnvironment, Monitor, init_logging, acquire_lock, release_lock

running = True

def signal_handler(signum, frame):
    global running
    running = False

def main():
    if not acquire_lock():
        sys.exit(1)
        
    try:
        init_logging()
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        env = SystemEnvironment()
        monitor = Monitor(env, stop_event=lambda: not running)
        monitor.run_loop()
    finally:
        release_lock()

if __name__ == "__main__":
    main()
