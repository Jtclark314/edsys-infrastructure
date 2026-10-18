using System;

public static class TestWakeEpoch {
    static int count;
    static void Check(bool value,string why) { if(!value) throw new Exception(why);count++; }
    public static void Main() {
        var g=new WakeEpoch();
        Check(!g.Enabled && g.Phase=="off", "initially disabled");
        Check(!g.Wake(0),"initial wake rejected");
        g.Ready();Check(g.Phase=="off","unsolicited ready ignored");
        g.Enable();Check(g.Enabled && g.Phase=="starting","enable does not arm");
        Check(!g.Wake(0),"loading cannot dispatch");
        g.Ready();Check(g.Phase=="disarmed","ready stays disarmed");
        int first=g.Command(true);
        Check(!g.Wake(first),"no wake before capture-start acknowledgement");
        Check(!g.Acknowledge("armed",first+1),"future ack rejected");
        Check(!g.Acknowledge("disarmed",first),"wrong ack rejected");
        Check(g.Acknowledge("armed",first),"fresh arm accepted");
        Check(g.Wake(first),"fresh wake after arm accepted");
        Check(!g.Wake(first),"duplicate wake rejected");
        int second=g.Command(true);g.Acknowledge("armed",second);
        Check(!g.Wake(first),"prior capture wake rejected");
        int release=g.Command(false);
        Check(!g.Wake(second),"wake racing manual Alt+V release rejected");
        Check(!g.Acknowledge("armed",second),"late arm cannot undo release");
        Check(g.Acknowledge("disarmed",release),"release ack accepted");
        int next=g.Command(true);g.Acknowledge("armed",next);g.Disable();
        Check(!g.Wake(next),"disable retires queued wake");
        Check(!g.Acknowledge("armed",next),"disabled old arm rejected");
        g.Enable();g.Ready();int after=g.Command(true);g.Acknowledge("armed",after);
        Check(after>next,"epochs survive worker restart");
        Check(!g.Wake(next),"previous worker event rejected");
        Check(g.Wake(after),"new explicit enable works");
        g.Disable();Check(!g.Enabled && g.Phase=="off","disable leaves no armed session");
        Console.WriteLine(count+" wake epoch checks passed");
    }
}
