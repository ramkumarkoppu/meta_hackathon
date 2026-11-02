"""Locked Computer Assist: AI help for a locked computer through a USB KVM.

The KVM's capture side feeds the nanokvm service (service.py), Muse Glimmer reads the frames on this laptop
(glimmer.py), redacted text goes to Muse Spark only through the outbound gate (outbound.py), and approved edits and
steps are typed and clicked back through the KVM's keyboard and mouse (editing.py, acting.py, hid.py).
"""

__version__ = "0.1.0"
