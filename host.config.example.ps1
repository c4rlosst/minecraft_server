# Copy this file to host.config.ps1 (it is git-ignored) and edit it for THIS PC.

# Folder every host syncs through Google Drive / Dropbox / OneDrive. The same shared folder on every PC,
# but the local path may differ. Set it to "Available offline" / "Mirror files" so files are really on disk.
$SharedDir = "C:\Users\YOU\Google Drive\SMP-World"

# Your name, shown to friends when you are hosting.
$HostName = "Carlos"

# playit.gg agent. "playit" works if it's on PATH (winget install DevelopedMethods.playit);
# otherwise give the full path to the exe. Leave empty to run your tunnel yourself.
$PlayitExe = "playit"

# The SHARED agent secret key (from your playit.gg dashboard) so every host gets the same tunnel address.
# Get it from whoever created the tunnel, privately. Never commit it or put it in the shared cloud folder.
$PlayitSecret = ""
