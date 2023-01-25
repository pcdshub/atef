"""
Report rendering framework
"""

import hashlib
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, List, Optional, Tuple, Union

from reportlab import platypus
from reportlab.lib import colors, pagesizes, units
from reportlab.lib.styles import ParagraphStyle as PS
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.flowables import Flowable
from reportlab.platypus.frames import Frame
from reportlab.platypus.paragraph import Paragraph
from reportlab.platypus.tableofcontents import TableOfContents

from atef.check import Result
from atef.config import (PreparedComparison, PreparedConfiguration,
                         PreparedDeviceConfiguration, PreparedFile,
                         PreparedGroup, PreparedPVConfiguration,
                         PreparedSignalComparison, PreparedToolComparison,
                         PreparedToolConfiguration)
from atef.enums import Severity

h1 = PS(name='Heading1', fontSize=16, leading=20)

h2 = PS(name='Heading2', fontSize=12, leading=13, leftIndent=5)

l0 = PS(name='list0', fontSize=12, leading=15, leftIndent=0,
        rightIndent=0, spaceBefore=12, spaceAfter=0)

styles = getSampleStyleSheet()


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
            ['', '', 'Date:', str(datetime.today().date())],
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
        toc = TableOfContents(dotsMinLevel=0)
        # For conciseness we use the same styles for headings and TOC entries
        toc.levelStyles = [h1, h2]
        story.append(platypus.NextPageTemplate('cover'))
        story.append(Paragraph('Checkout Report',
                               PS('cover_title', fontSize=20, leading=22)))
        # story.append(self.LOGO)
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

    def create_report(self) -> None:
        """ Build the final report. """
        raise NotImplementedError()


class PassiveAtefReport(AtefReport):
    """
    Report for Passive Checkouts.  Assumes specific PreparedFile structure
    """
    def create_report(self):
        """ Use the stored config and create the report """
        # Build story as a list of Flowable objects
        story = []
        self.build_cover_page(story)
        self.build_summary(story)
        for c, _ in self.walk_config_file(self.config.root):
            self.build_config_page(story, c)

        story.append(Paragraph('Last heading', h1))

        # must build several times to place items then gather info for ToC
        self.multiBuild(story)

    def build_summary(self, story: List[Flowable]):
        """ Build summary table for checkout """
        story.append(self.build_linked_header('Checkout Summary', h1))
        story.append(platypus.Spacer(width=0, height=.5*cm))
        # table with results
        lines = list(self.walk_config_file(self.config.root))
        table_data = [['Step Name', 'Result']]
        style = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
                 ('ALIGN', (0, 0), (1, 0), 'CENTER'),
                 ('BOX', (0, 0), (-1, -1), 1, colors.black),
                 ('BOX', (0, 0), (0, -1), 1, colors.black)]
        for i in range(len(lines)):
            # content
            item, level = lines[i]
            prefix = '    ' * level
            if isinstance(item, PreparedConfiguration):
                name = item.config.name
            elif isinstance(item, PreparedComparison):
                name = item.comparison.name
            name = name or type(item).__name__
            table_data.append(
                [
                    prefix + f'{name}',
                    self.get_result_text(item.result)
                ]
            )

            # style
            if isinstance(item, PreparedConfiguration):
                style.append(['LINEABOVE', (0, i+1), (-1, i+1), 1, colors.black])
            else:
                style.append(['LINEABOVE', (0, i+1), (-1, i+1), 1, colors.lightgrey])

        table = platypus.Table(
            table_data, style=style
        )

        story.append(table)
        story.append(platypus.PageBreak())

    def walk_config_file(
        self,
        config,
        level: int = 0
    ) -> Generator[Tuple[Any, int], None, None]:
        """ Start with top-level group, not PreparedFile"""
        yield config, level

        if isinstance(config, PreparedConfiguration):
            if hasattr(config, 'configs'):
                for conf in config.configs:
                    yield from self.walk_config_file(conf, level=level+1)
            if hasattr(config, 'comparisons'):
                for comp in config.comparisons:
                    yield from self.walk_config_file(comp, level=level+1)

    def get_result_text(self, result: Result) -> Paragraph:
        severity = result.severity
        result_colors = {
            Severity.error: 'red',
            Severity.internal_error: 'yellow',
            Severity.success: 'green',
            Severity.warning: 'orange'
        }

        text = (f'<font color={result_colors[severity]}>'
                f'<b>{severity.name}</b>: {result.reason or "-"}</font>')
        return Paragraph(text)

    def build_config_page(self, story: List[Flowable], config) -> None:
        """ Build a config/comparison page.  Dispatches to helpers """
        # section title and hyperlink
        desc = getattr(config, 'description', None)
        if desc:
            story.append(Paragraph(f'{getattr}'), PS('body'))

        # render individual results
        self.build_settings_results(story, config)

        # end of section
        story.append(platypus.PageBreak())

    def build_settings_results(self, story: List[Flowable], config):
        """ Table with settings and results of sub-comparisons """

        if isinstance(config, (PreparedGroup, PreparedDeviceConfiguration,
                      PreparedPVConfiguration, PreparedToolConfiguration)):
            self.build_default_page(story, config, kind='config')
        elif isinstance(config, (PreparedSignalComparison, PreparedToolComparison)):
            self.build_default_page(story, config, kind='comparison')
            self.build_data_table(story, config)
        else:
            config_type = str(type(config).__name__)
            header = self.build_linked_header(config_type, h1)
            story.append(header)
            story.append(Paragraph('page format not found'))

    def build_default_page(
        self,
        story: List[Flowable],
        config: PreparedConfiguration,
        kind: str
    ) -> None:
        # Header bit
        setting_config = getattr(config, kind)
        header_text = setting_config.name
        story.append(self.build_linked_header(header_text, h1))
        story.append(Paragraph(setting_config.description))
        result = getattr(config, 'result', None)
        if result:
            story.append(self.get_result_text(result))
        # settings table
        story.append(Paragraph('Settings', l0))
        settings_data = []
        for field in fields(setting_config):
            if field.name not in ['name', 'description', 'by_pv',
                                  'by_attr', 'shared', 'configs']:
                settings_data.append(
                    [field.name,
                     Paragraph(str(getattr(setting_config, field.name)),
                               styles['BodyText'])]
                )
        settings_table = platypus.Table(
            settings_data,
            style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
        )
        story.append(settings_table)
        # results table
        results_data = []
        for comp in getattr(config, 'comparisons', []):
            results_data.append([f'{comp.comparison.name} - {comp.identifier}',
                                 self.get_result_text(comp.result)])
        if results_data:
            story.append(Paragraph('Results', l0))
            results_table = platypus.Table(
                results_data,
                style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
            )
            story.append(results_table)

    def build_data_table(
        self,
        story: List[Flowable],
        config: PreparedComparison
    ) -> None:
        story.append(Paragraph('Observed Data'))
        # use cached value.  If there is no value there it will
        # try to access...
        observed_value = config.data or 'N/A'
        try:
            timestamp = datetime.fromtimestamp(config.signal.timestamp).ctime()
            source = config.signal.name
        except AttributeError:
            timestamp = 'unknown'
            source = 'undefined'
        observed_data = [['Observed Value', 'Timestamp', 'Source'],
                         [observed_value, timestamp, source]]

        observed_table = platypus.Table(
            observed_data,
            style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
        )

        story.append(observed_table)
