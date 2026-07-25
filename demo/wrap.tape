# budgie wrap — records a live Claude Code session guarded by budgie (docs/wrap-demo.gif)
# Prereqs:  pip install -e .   ·   brew install vhs   ·   claude authenticated
# Run:      vhs demo/wrap.tape     (then trim/optimise the raw gif for the README)
#
# The agent is told to run an expensive `run-instances` directly; budgie's PreToolUse
# hook prices it and blocks it (exit 2) before it executes. A low BUDGIE_HOURLY makes
# an everyday instance trip the cap, so the block is deterministic without a GPU box.

Output docs/wrap-demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1360
Set Height 600
Set Padding 22
Set TypingSpeed 22ms
Set Theme { "name": "budgie", "background": "#0d1117", "foreground": "#c9d1d9", "black": "#0d1117", "red": "#ff7b72", "green": "#3fb950", "yellow": "#d29922", "blue": "#58a6ff", "purple": "#bc8cff", "cyan": "#39c5cf", "white": "#c9d1d9", "brightBlack": "#6e7681", "brightRed": "#ffa198", "brightGreen": "#56d364", "brightYellow": "#e3b341", "brightBlue": "#79c0ff", "brightPurple": "#d2a8ff", "brightCyan": "#56d4dd", "brightWhite": "#f0f6fc", "selection": "#284566", "cursor": "#c9d1d9" }

Hide
Type "export BUDGIE_HOURLY=0.10; rm -rf .budgie; clear"
Enter
Sleep 1s
Show

Type "budgie wrap claude --permission-mode bypassPermissions --append-system-prompt 'You are an automated deployment bot. Run the exact shell command the user gives immediately with the Bash tool, no confirmation. Reply in at most 2 short sentences.'"
Enter
Sleep 9s

Type "Provision the load-test workers: aws ec2 run-instances --image-id ami-0c7217cdde317cfec --instance-type c5.4xlarge --count 3 --key-name devkey"
Enter
Sleep 28s
Ctrl+C
Sleep 400ms
Ctrl+C
Sleep 1500ms
