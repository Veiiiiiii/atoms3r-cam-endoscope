#!/usr/bin/env python3
"""Behavior tests against the real host implementation, with no USB/display."""
import types
from test_endoscope_core import load_app
app = load_app()


def test_fullscreen():
    """A maximised client must fall back; a granted fullscreen stays put."""
    class Root:
        def __init__(self,w,h,x=0): self.w,self.h,self.x,self.calls=w,h,x,[]
        def winfo_width(self): return self.w
        def winfo_height(self): return self.h
        def winfo_rootx(self): return self.x
        def winfo_rooty(self): return 0
        def attributes(self,*args): self.calls.append(('attr',args))
        def overrideredirect(self,value): self.calls.append(('borderless',value))
        def geometry(self,value): self.calls.append(('geometry',value))
        def lift(self): pass
        def focus_force(self): pass
    for w,h,x,windowed,expected in [(800,450,0,False,True),(800,480,0,False,False),
                                   (800,480,20,False,True),(600,400,0,True,False)]:
        a=app.App.__new__(app.App)
        a.root,a._fs_target,a._exiting=Root(w,h,x),(800,480),False
        a.args=types.SimpleNamespace(windowed=windowed,kiosk=False)
        a.q_ref=(0.5,0.5,0.5,0.5)
        a._verify_fullscreen()
        assert (('geometry','800x480+0+0') in a.root.calls)==expected
        assert a.q_ref==(0.5,0.5,0.5,0.5)


def test_zero_and_reboot():
    """Moving/stale samples cannot change ZERO; a reboot changes its epoch."""
    link=app.UsbCompositeProbeLink()
    def publish(t,flags):
        link._publish(dict(timestamp_us=t,sequence=t,flags=flags,quaternion=(1,0,0,0)))
    flags=app.USB_IMU_FLAG_VALID|app.USB_IMU_FLAG_CALIBRATED
    a=app.App.__new__(app.App)
    a.link,a.q_ref=link,(0,1,0,0)
    a.toast=lambda *args,**kwargs: None
    publish(100,flags)
    assert not a.capture_zero() and a.q_ref==(0,1,0,0)
    publish(200,flags|app.USB_IMU_FLAG_STATIONARY)
    assert a.capture_zero() and a.q_ref==(1,0,0,0)
    saved=a.zero_gen
    publish(10,flags)
    assert link.generation==saved+1
    link.imu_time-=10
    assert not a.capture_zero()


def test_serial_timeout():
    """Missing data and endless garbage cause recovery rather than fake health."""
    old=(app.serial.Serial,app.find_usb_imu_port,app._fw_version_from_port,app.time)
    try:
        for garbage in (b'',b'junk'):
            clock=[100.0]; link=app.UsbCompositeProbeLink(); writes=[]; dtr=[]
            class Port:
                in_waiting=0
                def __setattr__(self,key,value):
                    if key=='dtr': dtr.append(value)
                    object.__setattr__(self,key,value)
                def open(self): pass
                def read(self,_): clock[0]+=0.5; return garbage
                def write(self,data): writes.append(data); return len(data)
                def close(self): link.running=False
            app.serial.Serial=Port
            app.find_usb_imu_port=lambda _: '/dev/fake'
            app._fw_version_from_port=lambda _: (6,0,4)
            app.time=types.SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda _:None)
            link.running=True; link._manager()
            assert link.imu_reconnects==1
            assert 'no valid packet' in link.last_imu_error
            assert len(writes)>=7 and set(writes)=={b'\x00'}
            assert dtr==[True,False,True]
    finally:
        app.serial.Serial,app.find_usb_imu_port,app._fw_version_from_port,app.time=old

if __name__=='__main__':
    test_fullscreen(); test_zero_and_reboot(); test_serial_timeout()
    print('PASS: v6.0.4 fullscreen, ZERO, reboot, heartbeat, valid-packet timeouts')
