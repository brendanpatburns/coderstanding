using Geometry.Shapes;
using System.Collections.Generic;

namespace Geometry.App
{
    public partial class Canvas
    {
        private readonly Renderer _renderer = new Renderer();
        private IShape _selected;
        private List<Circle> _circles = new List<Circle>();
        public Logger Log { get; set; }

        public void Draw(Square square)
        {
            _renderer.Paint(square);
            _selected.Area();
            Log.Info("drawn");
            var tmp = new Circle();
            Corner corner = null;
            MathUtil.Clamp(1, 2);
        }
    }
}
