# -*- coding: utf-8 -*-
"""BOKI ESTRUCTURAS brand in every window's header: the header's "BOKI"
text becomes "BOKI ESTRUCTURAS" with the seal image beside it (the whole
image, in a thin orange frame). One place for all the windows; a window
without the header is left as it is."""
import os

LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "BOKI Estructura.tab", "_recursos", "boki_logo.png")
ORANGE, LIGHT = "#FFFFB347", "#FFFFFFFF"


def _find_boki(root):
    """The header's TextBlock reading "BOKI" (logical tree)."""
    from System.Windows import LogicalTreeHelper, DependencyObject
    from System.Windows.Controls import TextBlock
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, TextBlock) and (node.Text or u"").strip() == u"BOKI":
            return node
        if isinstance(node, DependencyObject):
            for child in LogicalTreeHelper.GetChildren(node):
                stack.append(child)
    return None


def _add_header(window):
    """A window built without the BOKI header gets one like the others
    (dark band: its title, and "BOKI" on the right, branded by apply).
    Returns the "BOKI" TextBlock, or None if the window can't take it."""
    from System.Windows import Thickness, VerticalAlignment, FontWeights
    from System.Windows.Controls import TextBlock, Border, DockPanel, Dock
    from System.Windows.Media import BrushConverter
    content = window.Content
    if content is None:
        return None
    brush = BrushConverter()
    band = Border()
    band.Background = brush.ConvertFromString("#FF16324F")
    band.Padding = Thickness(16, 8, 16, 8)
    inner = DockPanel()
    boki = TextBlock()
    boki.Text = u"BOKI"
    boki.Foreground = brush.ConvertFromString(ORANGE)
    boki.FontSize = 20
    boki.FontWeight = FontWeights.Bold
    boki.VerticalAlignment = VerticalAlignment.Center
    DockPanel.SetDock(boki, Dock.Right)
    inner.Children.Add(boki)
    title = TextBlock()
    title.Text = (window.Title or u"").upper()
    title.Foreground = brush.ConvertFromString(LIGHT)
    title.FontSize = 18
    title.FontWeight = FontWeights.Bold
    title.VerticalAlignment = VerticalAlignment.Center
    inner.Children.Add(title)
    band.Child = inner
    window.Content = None
    root = DockPanel()
    DockPanel.SetDock(band, Dock.Top)
    root.Children.Add(band)
    root.Children.Add(content)
    window.Content = root
    return boki


def apply(window):
    """Put the brand in `window` (a WPF window already loaded); one
    without the header gets it first (2026-11-25: all windows alike)."""
    from System import Uri, UriKind
    from System.Windows import Thickness, VerticalAlignment, FontWeights, CornerRadius
    from System.Windows.Controls import StackPanel, Orientation, TextBlock, Border, Image, DockPanel
    from System.Windows.Documents import Run
    from System.Windows.Media import BrushConverter, Stretch
    from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
    old = _find_boki(window) or _add_header(window)
    if old is None:
        return False
    parent = old.Parent
    brush = BrushConverter()
    panel = StackPanel()
    panel.Orientation = Orientation.Horizontal
    panel.VerticalAlignment = VerticalAlignment.Center
    DockPanel.SetDock(panel, DockPanel.GetDock(old))
    text = TextBlock()
    text.VerticalAlignment = VerticalAlignment.Center
    text.FontSize = 18
    text.FontWeight = FontWeights.Bold
    text.Margin = Thickness(0, 0, 10, 0)
    r1 = Run(u"BOKI ")
    r1.Foreground = brush.ConvertFromString(ORANGE)
    r2 = Run(u"ESTRUCTURAS")
    r2.Foreground = brush.ConvertFromString(LIGHT)
    text.Inlines.Add(r1)
    text.Inlines.Add(r2)
    panel.Children.Add(text)
    if os.path.isfile(LOGO):
        bmp = BitmapImage()
        bmp.BeginInit()
        bmp.UriSource = Uri(LOGO, UriKind.Absolute)
        bmp.CacheOption = BitmapCacheOption.OnLoad
        bmp.EndInit()
        img = Image()
        img.Source = bmp
        img.Height = 52
        img.Stretch = Stretch.Uniform
        frame = Border()
        frame.CornerRadius = CornerRadius(6)
        frame.BorderBrush = brush.ConvertFromString(ORANGE)
        frame.BorderThickness = Thickness(1.5)
        frame.Padding = Thickness(1)
        frame.Child = img
        panel.Children.Add(frame)
    children = getattr(parent, "Children", None)
    if children is None:
        return False
    index = children.IndexOf(old)
    children.RemoveAt(index)
    children.Insert(index, panel)
    return True
