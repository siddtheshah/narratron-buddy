"""Canonical objects follow the fitted scene through resize, crop, and zoom."""
from collections.abc import Iterator
from pathlib import Path
import sys

from playwright.sync_api import Page, sync_playwright
import pytest


@pytest.fixture
def geometry_page() -> Iterator[Page]:
    renderer = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")
    renderer = renderer[renderer.index("export function createDoodleRenderer("):].replace("export function", "function")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=2)
        page.set_content('''<style>
            #scene {position:relative;width:600px;height:400px;transform-origin:0 0}
            canvas {width:100%;height:100%}
            #stamps,#texts {position:absolute;inset:0}
            .canvas-stamp-annotation,.canvas-text-annotation {position:absolute}
            .canvas-stamp-annotation img {width:100%;height:100%}
            .canvas-text-annotation {color:transparent;white-space:pre}
        </style><div id="scene"><canvas></canvas><div id="stamps"></div><div id="texts"></div></div>''')
        page.add_script_tag(content=renderer)
        page.add_script_tag(content='''
            const canvas = document.querySelector('canvas');
            const scene = document.getElementById('scene');
            const bitmap = document.createElement('canvas'); bitmap.width=200; bitmap.height=50;
            bitmap.getContext('2d').fillRect(0,0,200,50);
            const actions = [
                {type:'stamp',id:'piece',url:bitmap.toDataURL(),x:.5,y:.5,size:80,rotation:90,
                 canvas_width:1000,canvas_height:500,canvas_page_width:2000,canvas_page_height:1000},
                {type:'text',id:'label',text:'Hello',x:.1,y:.2,size:32,canvas_font_size:640,
                 canvas_width:1200,canvas_height:736,canvas_page_width:2000,canvas_page_height:1000},
                {type:'draw',id:'segment',stroke_id:'stroke',x0:.1,y0:.2,x1:.8,y1:.7,size:3,color:'red',
                 canvas_line_width:30,canvas_page_width:2000,canvas_page_height:1000}
            ];
            let renderer;
            function start(fitMode) {
                document.getElementById('stamps').replaceChildren();
                document.getElementById('texts').replaceChildren();
                renderer = createDoodleRenderer({canvas,fitMode,getPage:()=>({width:2000,height:1000}),
                    stampLayer:document.getElementById('stamps'),textLayer:document.getElementById('texts')});
                renderer.resize(actions);
            }
            start('contain');
        ''')
        yield page
        browser.close()


@pytest.mark.parametrize("fit_mode", ["contain", "cover"])
def test_canonical_layout_matches_image_plane_across_viewports_and_zoom(geometry_page: Page, fit_mode: str) -> None:
    geometry_page.evaluate("fit => start(fit)", fit_mode)
    for width, height, zoom in [(600, 400, 1), (400, 600, 1), (600, 400, 2), (800, 400, 0.5)]:
        layout = geometry_page.evaluate('''({width,height,zoom})=>{
            scene.style.width=`${width}px`;scene.style.height=`${height}px`;
            scene.style.transform=`translate(30px,20px) scale(${zoom})`;
            renderer.resize(actions);
            const plane=renderer.imagePlane(actions[0]);
            const stamp=document.querySelector('.canvas-stamp-annotation').getBoundingClientRect();
            const label=document.querySelector('.canvas-text-annotation');
            const rect=canvas.getBoundingClientRect();
            const pointer=renderer.normalizedPoint({clientX:rect.left+(plane.x+plane.width*.5)*zoom,
                clientY:rect.top+(plane.y+plane.height*.5)*zoom});
            return {plane,stamp:{width:stamp.width,height:stamp.height,cx:(stamp.left+stamp.right)/2,cy:(stamp.top+stamp.bottom)/2},
                rect:{left:rect.left,top:rect.top},pointer,
                font:parseFloat(label.style.fontSize),labelWidth:parseFloat(label.style.width),
                hit:renderer.findSegmentAt((plane.x+plane.width*.1)*zoom,(plane.y+plane.height*.2)*zoom)?.stroke_id};
        }''', {"width": width, "height": height, "zoom": zoom})
        scale = max(width / 2000, height / 1000) if fit_mode == "cover" else min(width / 2000, height / 1000)
        image_width, image_height = 2000 * scale, 1000 * scale
        assert layout["plane"]["width"] == pytest.approx(image_width)
        assert layout["plane"]["height"] == pytest.approx(image_height)
        assert layout["stamp"]["width"] == pytest.approx(image_height * 0.05 * zoom, abs=0.05)
        assert layout["stamp"]["height"] == pytest.approx(image_width * 0.1 * zoom, abs=0.05)
        assert layout["stamp"]["cx"] == pytest.approx(layout["rect"]["left"] + width * zoom / 2, abs=0.05)
        assert layout["stamp"]["cy"] == pytest.approx(layout["rect"]["top"] + height * zoom / 2, abs=0.05)
        assert layout["font"] == pytest.approx(image_height * 0.064)
        assert layout["labelWidth"] == pytest.approx(image_width * 0.12)
        assert layout["pointer"] == {"x": 0.5, "y": 0.5}
        assert layout["hit"] == "stroke"
