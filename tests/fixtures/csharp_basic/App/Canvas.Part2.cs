using Geometry.Shapes;

namespace Geometry.App
{
    public partial class Canvas
    {
        private Palette _palette;

        public Canvas(Palette palette)
        {
            _palette = palette;
        }

        public IShape Pick() => _selected;

        public void Hook(Button button)
        {
            button.Clicked += OnClicked;
        }

        private void OnClicked() { }
    }

    public class Renderer
    {
        public void Paint(IShape shape) { }
    }

    public class Palette { }
    public class Logger { public void Info(string text) { } }
    public static class MathUtil { public static int Clamp(int a, int b) => a; }
    public class Button { public event System.Action Clicked; }

    public class Settings
    {
        public static Settings Instance { get; } = new Settings();
        public enum Mode { Fast, Slow }
        public record Point(int X, int Y);
    }
}
