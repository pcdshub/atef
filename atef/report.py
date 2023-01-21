"""
Report rendering framework
"""

import hashlib
import html
from pathlib import Path
from typing import Any, List, Optional, Union

from reportlab import platypus
from reportlab.lib import colors, pagesizes, units
from reportlab.lib.styles import ParagraphStyle as PS
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.flowables import Flowable
from reportlab.platypus.frames import Frame
from reportlab.platypus.paragraph import Paragraph
from reportlab.platypus.tableofcontents import TableOfContents

from atef.config import PreparedFile

h1 = PS(name='Heading1', fontSize=14, leading=16)

h2 = PS(name='Heading2', fontSize=12, leading=14, leftIndent=5)

l0 = PS(name='list0', fontSize=12, leading=15, leftIndent=0,
        rightIndent=0, spaceBefore=12, spaceAfter=0)


class AtefReport(BaseDocTemplate):
    """
    Document template to auto-gather table of contents and manage styles
    Also holds config as an attempt to consolidate information, settings

    Should be initialized with the report file path and configuration file
    to render.

    .. code-block:: python

        doc = AtefReport('/path/to/pdf/location/report.pdf, config=file)

    Extra information can be passed at init or configured prior to file
    creation

    .. code-block:: python

        doc.set_info(author='JoeShmo', version='v1.0.1')
        doc.create_report()

    """
    LOGO = platypus.Image(Path(__file__).parent / 'assets' / 'SLAC_short_red.jpeg',
                          kind='proportional', width=6.0*units.inch,
                          height=6.0*units.inch)

    def __init__(
        self,
        filename: str,
        config: Union[PreparedFile, Any],
        pagesize=pagesizes.letter,
        author: str = 'unknown',
        version: str = '0.0.1',
        **doc_kwargs
    ):
        self.allowSplitting = 0
        super().__init__(filename, pagesize=pagesize, **doc_kwargs)

        template_cover_page = PageTemplate(
            'cover',
            [Frame(1.75*cm, 2.5*cm, 17.5*cm, 24*cm, id='F1')]
        )
        template_normal_page = PageTemplate(
            'normal',
            [Frame(1.75*cm, 2.5*cm, 17.5*cm, 22*cm, id='F1')],
            onPage=self.header_and_footer
        )
        self.addPageTemplates([template_cover_page, template_normal_page])

        self.config = config
        self.author = author
        self.version = version
        self.header_center_text = 'Atef File Name'
        self.footer_center_text = 'footer center text'

    def afterFlowable(self, flowable: Flowable) -> None:
        """ Registers TOC entries. """
        if flowable.__class__.__name__ == 'Paragraph':
            text = flowable.getPlainText()
            style = flowable.style.name
            if style == 'Heading1':
                entry_data = [0, text, self.page]
            elif style == 'Heading2':
                entry_data = [1, text, self.page]
            else:
                return

            link_name = getattr(flowable, '_bookmark_name', None)
            if link_name:
                entry_data.append(link_name)

            self.notify('TOCEntry', tuple(entry_data))

    def header_and_footer(self, canvas: Canvas, doc: BaseDocTemplate) -> None:
        """
        Callback that draws both header and footer.
        doc included to match callback signature, but is identical to self
        """
        self.build_header(canvas)
        self.build_footer(canvas)

    def build_header(self, canvas: Canvas) -> None:
        """ Build header table """
        canvas.saveState()

        header_table_data = [
            ['', self.header_center_text, 'Author:', f"{self.author}"],
            ['', '', 'Date:', '11.22.3333'],
            ['', '', 'Version:', f'{self.version}']
        ]

        table = platypus.Table(
            header_table_data,
            colWidths=[4*cm, 9.5*cm, 1.6*cm, 2.4*cm],
            rowHeights=[0.66*cm, 0.66*cm, 0.66*cm],
            style=[
                ('GRID', (0, 0), (1, 2), 1, colors.black),
                ('ALIGN', (0, 0), (0, 3), 'CENTER'),
                ('VALIGN', (0, 0), (3, 2), 'MIDDLE'),
                ('ALIGN', (2, 0), (3, 2), 'RIGHT'),
                ('SPAN', (0, 0), (0, 2)),
                ('SPAN', (1, 0), (1, 2)),
                ('BOX', (2, 0), (-1, -1), 1, colors.black),
            ]
        )

        w, h = table.wrap(self.width, self.topMargin)
        table.drawOn(
            canvas,
            self.leftMargin - 22.4,
            self.height + self.bottomMargin + self.topMargin - h - 25
        )
        canvas.restoreState()

    def build_footer(self, canvas: Canvas) -> None:
        canvas.saveState()
        footer_caption = self.footer_center_text
        page_num = f'Page: {canvas.getPageNumber()}'

        table_data = [['', footer_caption, page_num]]
        # Key, start(C,R), end(C,R), Setting
        table = platypus.Table(
            table_data,
            colWidths=[4*cm, 9.5*cm, 4*cm],
            rowHeights=[1*cm],
            style=[
                ('GRID', (0, 0), (2, 0), 1, colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('ALIGN', (0, 0), (3, 0), 'CENTER'),
            ]
        )

        w, h = table.wrap(self.width, self.bottomMargin)
        table.drawOn(canvas, self.leftMargin - 22.4, h)
        canvas.restoreState()

    def set_info(
        self,
        author: Optional[str] = None,
        version: Optional[str] = None
    ) -> None:
        if author:
            self.author = author
        if version:
            self.verion = version

    def build_cover_page(self, story: List[Flowable]) -> None:
        toc = TableOfContents()
        # For conciseness we use the same styles for headings and TOC entries
        toc.levelStyles = [h1, h2]
        story.append(platypus.NextPageTemplate('cover'))
        story.append(Paragraph('Checkout Report',
                               PS('cover_title', fontSize=20, leading=22)))
        story.append(self.LOGO)
        story.append(Paragraph('Document Approval', l0))
        table_data = [
            ['Name:', 'Role:', 'Signature:', 'Date Approved:'],
            ['', '', '', '']
        ]
        # Key, start(C,R), end(C,R), Setting
        approval_table = platypus.Table(
            table_data,
            colWidths=[3.5*cm, 5*cm, 5*cm, 3*cm],
            rowHeights=[1*cm, 1.5*cm],
            style=[
                ('GRID', (0, 0), (3, 1), 1, colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('ALIGN', (0, 0), (3, 0), 'CENTER'),
            ]
        )
        story.append(approval_table)
        story.append(platypus.NextPageTemplate('normal'))
        story.append(platypus.PageBreak())
        story.append(Paragraph('Table of Contents',
                               PS('cover_title', fontSize=20, leading=22)))
        story.append(toc)
        story.append(platypus.PageBreak())

    def build_linked_header(self, text: str, style: PS) -> Paragraph:
        mark_name = (text+style.name).encode('utf-8')
        bookmark_name = hashlib.sha1(mark_name).hexdigest()
        header = Paragraph(text + f'<a name="{bookmark_name}"/>', style)
        header._bookmark_name = bookmark_name
        return header

    def build_results(self, config):
        """ tree view """
        return Paragraph('placeholder', l0)

    def build_config_page(self, story: List[Flowable], config) -> None:
        """ Page Logic goes HERE.  To be expanded and made much more complex """

        config_type = html.escape(str(type(config).__name__))
        header = self.build_linked_header(config_type, h1)
        story.append(header)

        results = self.build_results(config)
        story.append(results)
        story.append(Paragraph('Text in first heading', PS('body')))

        story.append(platypus.PageBreak())

    def create_report(self):
        """ Use the stored config and create the report """
        # Build story as a list of Flowable objects
        story = []
        self.build_cover_page(story)
        for c in self.config.root.configs:
            self.build_config_page(story, c)

        story.append(Paragraph('Last heading', h1))

        # must build several times to place items then gather info for ToC
        self.multiBuild(story)
