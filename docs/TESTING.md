# Testing Checklist

## Local Unit Tests
Run the Python unit tests (protocol and persistence) via pytest:

```bash
pip install -e .[dev]
pytest
```

## SocketCAN Loopback (vcan) Smoke Test
1. Create a virtual CAN interface:
   ```bash
   sudo modprobe vcan
   sudo ip link add dev vcan0 type vcan
   sudo ip link set up vcan0
   ```
2. Launch the `dm_tui.app` module pointing at the virtual channel:
   ```bash
   python -m dm_tui.app --channel vcan0  # CLI wrapper TBD
   ```
3. Use `can-utils` tools for loopback validation:
   ```bash
   cansend vcan0 201#00000000
   candump vcan0
   ```

These steps validate the BusManager abstraction before touching real hardware.
