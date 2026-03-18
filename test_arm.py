from arm_controller import RoboticArm
import time

# Use the correct port (change if necessary)
arm = RoboticArm(port='/dev/ttyUSB1', baudrate=9600, mock=False)

# Wait a moment for connection
time.sleep(1)

# Test home
arm.home()
time.sleep(10)  # Wait for movement to complete

# Test open/close
arm.open_gripper()
time.sleep(3)
arm.close_gripper()
time.sleep(3)

# Test pickup
arm.pick_up("plastic")
time.sleep(30)  # Wait for full sequence

# Return home
arm.home()
time.sleep(10)

# Clean up
arm.stop()