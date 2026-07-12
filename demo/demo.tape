# budgie demo — records docs/demo.gif
# Prereqs:  pip install -e .   &&   brew install vhs   (github.com/charmbracelet/vhs)
# Run:      vhs demo/demo.tape

Output docs/demo.gif
Require budgie

Set Shell "bash"
Set FontSize 18
Set Width 1480
Set Height 460
Set Padding 24
Set TypingSpeed 28ms
Set Theme { "name": "budgie", "background": "#0d1117", "foreground": "#c9d1d9", "black": "#0d1117", "red": "#ff7b72", "green": "#3fb950", "yellow": "#d29922", "blue": "#58a6ff", "purple": "#bc8cff", "cyan": "#39c5cf", "white": "#c9d1d9", "brightBlack": "#6e7681", "brightRed": "#ffa198", "brightGreen": "#56d364", "brightYellow": "#e3b341", "brightBlue": "#79c0ff", "brightPurple": "#d2a8ff", "brightCyan": "#56d4dd", "brightWhite": "#f0f6fc", "selection": "#284566", "cursor": "#c9d1d9" }

# BUDGIE_HOURLY + BUDGIE_HOME are exported (and the session pre-seeded) by the
# caller before `vhs demo/demo.tape`, so the shell here inherits them cleanly.

Type "# budgie prices an agent's cloud command before it runs, per session."
Enter
Sleep 900ms

Type "# a cheap db class — but 20 TB of storage flips it over the cap:"
Enter
Sleep 700ms
Type "budgie check 'aws rds create-db-instance --db-instance-class db.t3.micro --allocated-storage 20000 --storage-type gp2'"
Enter
Sleep 2600ms

Type "# node groups are priced too — 40 nodes, not just the control plane:"
Enter
Sleep 700ms
Type "budgie check 'aws eks create-nodegroup --instance-types m5.24xlarge --scaling-config desiredSize=40'"
Enter
Sleep 2600ms

Type "# the obvious one — 2x the biggest GPU box — never runs:"
Enter
Sleep 700ms
Type "budgie check 'aws ec2 run-instances --instance-type p5.48xlarge --count 2'"
Enter
Sleep 2600ms

Type "# a small box passes straight through:"
Enter
Sleep 700ms
Type "budgie check 'aws ec2 run-instances --instance-type t3.micro'"
Enter
Sleep 2200ms

Type "# the session tracks live burn and dollars already spent, separately:"
Enter
Sleep 700ms
Type "budgie session"
Enter
Sleep 3000ms
