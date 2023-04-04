from java import jclass

from .label import LabelProbe


# On Android, a Button is just a TextView with a state-dependent background image.
class TextInputProbe(LabelProbe):
    native_class = jclass("android.widget.EditText")
