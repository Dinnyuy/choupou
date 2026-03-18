import serial
import threading
import time
from queue import Queue

class RoboticArm:
    """
    Controls a 4‑DOF robotic arm with a gripper via serial commands.
    Commands are queued and executed in a background thread.
    """
    def __init__(self, port='/dev/ttyUSB1', baudrate=9600, mock=False, **kwargs):
        # **kwargs allows extra arguments (like ack_timeout, queue_maxsize) to be ignored
        self.mock = mock
        self.command_queue = Queue()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.running = True
        self.last_command_time = 0
        self.command_delay = 0.5  # Minimum delay between commands (seconds)

        if not mock:
            try:
                self.ser = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=2,
                    write_timeout=2
                )
                time.sleep(2)  # Allow Arduino to reset

                # Clear any initial garbage (bootloader output)
                self.ser.reset_input_buffer()

                # Wait for the ready message
                if not self._wait_for_ready():
                    print("Warning: Did not receive 'Arm controller ready' from Arduino")
                else:
                    print("Arduino ready")

                print(f"Arm connected on {port} at {baudrate} baud")
            except Exception as e:
                print(f"Arm serial error: {e}. Switching to mock mode.")
                self.mock = True
        else:
            print("Arm running in MOCK mode (no hardware)")

        self.worker_thread.start()

    def _wait_for_ready(self, timeout=5):
        """Wait for the 'Arm controller ready' message."""
        start = time.time()
        while time.time() - start < timeout:
            if self.ser.in_waiting:
                line = self.ser.readline().decode().strip()
                if line:
                    print(f"Arduino: {line}")
                    if "Arm controller ready" in line:
                        return True
            time.sleep(0.1)
        return False

    def _read_until_ok(self, timeout=30):
        """
        Read all lines from Arduino until we get an 'OK' or 'ERROR' line.
        Returns the final status line ('OK' or 'ERROR ...').
        All intermediate lines are printed for debugging.
        """
        start_time = time.time()
        last_line = ""
        while time.time() - start_time < timeout:
            if self.ser.in_waiting:
                line = self.ser.readline().decode().strip()
                if line:
                    print(f"📨 Arduino: {line}")
                    last_line = line
                    if line == "OK" or line.startswith("ERROR"):
                        return line
            time.sleep(0.1)
        return last_line  # Return whatever we last received (maybe incomplete)

    def _worker(self):
        """Background thread that processes commands sequentially."""
        while self.running:
            waste_type = self.command_queue.get()
            if waste_type is None:      # shutdown signal
                break

            # Ensure minimum delay between commands
            elapsed = time.time() - self.last_command_time
            if elapsed < self.command_delay:
                time.sleep(self.command_delay - elapsed)

            self._execute_pickup(waste_type)
            self.last_command_time = time.time()
            self.command_queue.task_done()

    def _execute_pickup(self, waste_type):
        """Send the pickup command to the arm."""
        print(f"🤖 Arm executing pickup for: {waste_type}")

        if self.mock:
            # Simulate the sequence
            print("  → Moving to object...")
            time.sleep(1)
            print("  → Closing gripper...")
            time.sleep(0.5)
            print("  → Lifting...")
            time.sleep(0.5)
            print("  → Moving to drop zone...")
            time.sleep(1)
            print("  → Opening gripper...")
            time.sleep(0.5)
            print("  → Returning home...")
            time.sleep(1)
            print("✅ Pickup complete (MOCK)")
            return

        try:
            # Send PICKUP command
            self.ser.write(b"PICKUP\n")
            self.ser.flush()

            # Wait for the final OK
            response = self._read_until_ok()
            if response == "OK":
                print("✅ Arm completed pickup")
            else:
                print(f"❌ Arm error: {response}")

        except Exception as e:
            print(f"❌ Arm command failed: {e}")

    def home(self):
        """Send the arm to home position."""
        print("🔄 Sending arm to home position...")

        if self.mock:
            print("  → Moving to home...")
            time.sleep(1)
            print("✅ Home position reached (MOCK)")
            return

        try:
            self.ser.write(b"HOME\n")
            response = self._read_until_ok()
            if response == "OK":
                print("✅ Arm at home position")
            else:
                print(f"❌ Home command failed: {response}")
        except Exception as e:
            print(f"❌ Home command failed: {e}")

    def open_gripper(self):
        """Open the gripper."""
        if self.mock:
            print("Gripper opened (MOCK)")
            return

        try:
            self.ser.write(b"OPEN\n")
            response = self._read_until_ok(timeout=10)
            if response == "OK":
                print("✅ Gripper opened")
            else:
                print(f"❌ Open gripper failed: {response}")
        except Exception as e:
            print(f"❌ Open gripper failed: {e}")

    def close_gripper(self):
        """Close the gripper."""
        if self.mock:
            print("Gripper closed (MOCK)")
            return

        try:
            self.ser.write(b"CLOSE\n")
            response = self._read_until_ok(timeout=10)
            if response == "OK":
                print("✅ Gripper closed")
            else:
                print(f"❌ Close gripper failed: {response}")
        except Exception as e:
            print(f"❌ Close gripper failed: {e}")

    def move_to(self, base, elbow, wrist, gripper, delay=20):
        """Move to specific positions."""
        if self.mock:
            print(f"Moving to: base={base}, elbow={elbow}, wrist={wrist}, gripper={gripper} (MOCK)")
            return

        try:
            cmd = f"MOVE {base} {elbow} {wrist} {gripper} {delay}\n"
            self.ser.write(cmd.encode())
            response = self._read_until_ok()
            if response == "OK":
                print("✅ Move completed")
            else:
                print(f"❌ Move failed: {response}")
        except Exception as e:
            print(f"❌ Move command failed: {e}")

    def pick_up(self, waste_type):
        """Queue a pickup command for a given waste type."""
        print(f"📦 Queuing pickup for: {waste_type}")
        self.command_queue.put(waste_type)

    def stop(self):
        """Shut down the background thread and close serial port."""
        print("🛑 Stopping arm controller...")
        self.running = False
        self.command_queue.put(None)    # unblock worker

        if hasattr(self, 'worker_thread') and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2)

        if not self.mock and hasattr(self, 'ser'):
            try:
                self.ser.write(b"STOP\n")
                time.sleep(0.5)
                self.ser.close()
                print("Arm serial closed")
            except:
                pass