"""V2 protocol acceptance for automatic context, checkpoint and child requests.

Reuse the installed-client fixture so context and tool permissions are checked
on the same actual primary, title, delegated and continuation model requests.
No real model or cloud call is made.
"""
from qualify_profiles import main

if __name__ == '__main__':
    main()
