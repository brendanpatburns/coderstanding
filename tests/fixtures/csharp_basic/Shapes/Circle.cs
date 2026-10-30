namespace Geometry.Shapes
{
    public class Circle : IShape
    {
        public double Radius { get; set; }
        public double Area() => 3.14 * Radius * Radius;
    }
}
