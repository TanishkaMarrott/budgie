# budgie demo — records docs/demo.gif
# Prereqs:  pip install -e .   &&   brew install vhs   (github.com/charmbracelet/vhs)
# Run:      vhs demo/demo.tape

Output docs/demo.gif
Require budgie

Set Shell "bash"
Set FontSize 20
Set Width 1320
Set Height 760
Set Padding 24
Set Theme "Dracula"
Set TypingSpeed 30ms

Type "# 🐦 budgie — a spend firewall for AI agents"
Enter
Sleep 800ms

Type "# an agent tries to launch 2× the biggest GPU box..."
Enter
Sleep 800ms
Type "budgie check 'aws ec2 run-instances --instance-type p5.48xlarge --count 2'"
Enter
Sleep 2500ms

Type "# ...blocked before it runs. and it sees loops, too:"
Enter
Sleep 600ms
Type "budgie check 'for i in $(seq 100); do aws ec2 run-instances --instance-type p5.48xlarge; done'"
Enter
Sleep 2500ms

Type "# a cheap box passes straight through:"
Enter
Sleep 600ms
Type "budgie check 'aws ec2 run-instances --instance-type t3.micro'"
Enter
Sleep 2500ms

Type "# it's a PreToolUse hook — the command never even executes."
Enter
Sleep 2000ms
