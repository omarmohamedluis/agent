import subprocess
import sys

def test_ping(ip, timeout):
    print(f"Testing ping to {ip} with timeout {timeout}...")
    try:
        cmd = ["ping", "-c", "1", "-W", str(timeout), "-n", ip]
        print(f"Command: {' '.join(cmd)}")
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"Return code: {res.returncode}")
        print(f"Stdout: {res.stdout.strip()}")
        print(f"Stderr: {res.stderr.strip()}")
        return res.returncode == 0
    except Exception as e:
        print(f"Exception: {e}")
        return False

if __name__ == "__main__":
    target = "127.0.0.1"
    if len(sys.argv) > 1:
        target = sys.argv[1]
    
    print(f"--- Test 1: Timeout 1.0 ---")
    test_ping(target, 1.0)
    
    print(f"\n--- Test 2: Timeout 0.5 ---")
    test_ping(target, 0.5)
    
    print(f"\n--- Test 3: Timeout 0.2 ---")
    test_ping(target, 0.2)
