namespace Geometry.Shapes;

public abstract class Polygon
{
    public int Sides;
}

public class Square : Polygon, IShape
{
    public double Side;
    public double Area() => Side * Side;

    public class Corner
    {
        public int Angle = 90;
    }
}
