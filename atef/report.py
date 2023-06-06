"""
Report rendering framework
"""

import hashlib
import logging
from collections import defaultdict
from dataclasses import fields
from datetime import datetime
from operator import attrgetter
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

from reportlab import platypus
from reportlab.lib import colors, enums, pagesizes, units
from reportlab.lib.styles import ParagraphStyle as PS
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.flowables import Flowable
from reportlab.platypus.frames import Frame
from reportlab.platypus.paragraph import Paragraph
from reportlab.platypus.tableofcontents import TableOfContents

from atef.check import (Equals, Greater, GreaterOrEqual, Less, LessOrEqual,
                        NotEquals, Range)
from atef.config import (PreparedComparison, PreparedConfiguration,
                         PreparedDeviceConfiguration, PreparedFile,
                         PreparedGroup, PreparedPVConfiguration,
                         PreparedSignalComparison, PreparedToolComparison,
                         PreparedToolConfiguration)
from atef.enums import Severity
from atef.procedure import (PreparedPassiveStep, PreparedProcedureFile,
                            PreparedProcedureGroup, PreparedProcedureStep,
                            PreparedSetValueStep)
from atef.result import Result
from atef.type_hints import AnyDataclass

logger = logging.getLogger(__name__)

h1 = PS(name='Heading1', fontSize=16, leading=20)

h2 = PS(name='Heading2', fontSize=14, leading=17)

l0 = PS(name='list0', fontSize=12, leading=15, leftIndent=0,
        rightIndent=0, spaceBefore=12, spaceAfter=0)

styles = getSampleStyleSheet()

RESULT_COLOR = {
    Severity.error: 'red',
    Severity.internal_error: 'yellow',
    Severity.success: 'green',
    Severity.warning: 'orange'
}

symbol_map = {
    Equals: '=', NotEquals: '≠', Greater: '>', GreaterOrEqual: '≥', Less: '<',
    LessOrEqual: '≤', Range: '≤'
}


def walk_config_file(
    config: Union[PreparedFile, PreparedConfiguration, PreparedComparison],
    level: int = 0
) -> Generator[Tuple[Any, int], None, None]:
    """
    Yields each config and comparison and its depth
    Performs a recursive depth-first search

    Parameters
    ----------
    config : Union[PreparedFile, PreparedConfiguration, PreparedComparison]
        the configuration or comparison to walk
    level : int, optional
        the current recursion depth, by default 0

    Yields
    ------
    Generator[Tuple[Any, int], None, None]
    """
    yield config, level
    if isinstance(config, PreparedFile):
        yield from walk_config_file(config.root, level=level+1)
    elif isinstance(config, PreparedConfiguration):
        if hasattr(config, 'configs'):
            for conf in config.configs:
                yield from walk_config_file(conf, level=level+1)
        if hasattr(config, 'comparisons'):
            for comp in config.comparisons:
                yield from walk_config_file(comp, level=level+1)


def walk_procedure_file(
    config: Union[PreparedProcedureFile, PreparedProcedureStep, PreparedComparison],
    level: int = 0
) -> Generator[Tuple[Any, int], None, None]:
    """
    Yields each ProcedureStep / Comparison and its depth
    Performs a recursive depth-first search

    Parameters
    ----------
    config : Union[PreparedProcedureFile, PreparedProcedureStep,
                    PreparedComparison]
        the item to yield and walk through
    level : int, optional
        the current recursion depth, by default 0

    Yields
    ------
    Generator[Tuple[Any, int], None, None]
    """
    yield config, level
    if isinstance(config, PreparedProcedureFile):
        yield from walk_procedure_file(config.root, level=level+1)
    elif isinstance(config, PreparedProcedureStep):
        for sub_step in getattr(config, 'steps', []):
            yield from walk_procedure_file(sub_step, level=level+1)
        if hasattr(config, 'walk_comparisons'):
            for sub_comp in config.walk_comparisons():
                yield from walk_procedure_file(sub_comp, level=level+1)


def get_result_text(result: Result) -> Paragraph:
    """
    Reads a Result and returns a formatted Paragraph instance
    suitable for insertion into a story or platypus.Table

    Parameters
    ----------
    result : Result
        The result of a comparsion or group of comparisons

    Returns
    -------
    Paragraph
        A formatted, Flowable text object to be inserted into a story
    """
    severity = result.severity

    text = (f'<font color={RESULT_COLOR[severity]}>'
            f'<b>{severity.name}</b>: {result.reason or "-"}</font>')
    para = Paragraph(text)
    para.wrap(1.5*units.inch, 10*units.inch)
    return para


def build_passive_summary_table(story: List[Flowable], prep_file: PreparedFile):
    """
    Build a table summarizing that passive checkout described in ``prep_file``.
    Contains two columns, the step name and its result.
    Modifies the story in place, appending flowables to the end of the ``story``.

    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    prep_file : PreparedFile
        A prepared (and preferably run) passive checkout
    """
    if not prep_file:
        return
    lines = list(walk_config_file(prep_file.root))
    table_data = [['Step Name', 'Result']]
    style = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
             ('ALIGN', (0, 0), (1, 0), 'CENTER'),
             ('BOX', (0, 0), (-1, -1), 1, colors.black),
             ('BOX', (0, 0), (0, -1), 1, colors.black)]

    for i in range(len(lines)):
        # content
        item, level = lines[i]
        prefix = '    ' * level
        name = None
        if isinstance(item, PreparedConfiguration):
            name = item.config.name
        elif isinstance(item, PreparedComparison):
            name = f'{item.comparison.name} - {item.identifier}'
        name = name or type(item).__name__
        table_data.append(
            [
                prefix + f'{name}',
                get_result_text(item.result)
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


def build_action_check_table(
    story: List[Flowable],
    step: PreparedSetValueStep
) -> None:
    """
    Builds two tables, one for actions, one for the checks.
    The actions table will contain columns describing the
    (name, target, value, timestamp, result) of each action
    The checks table will contain columns describing the
    (name, timestamp, result) of each check/criteria
    Modifies the story in place, appending flowables to the end of the ``story``

    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    step : PreparedSetValueStep
        The SetValueStep with information needed to build the table
    """
    action_data = [['Name', 'Target', 'Value', 'Timestamp', 'Result']]
    for action in step.prepared_actions:
        # a list of PreparedValueToSignal
        timestamp = action.result.timestamp.ctime()
        action_data.append([action.name, action.signal.name, action.value,
                            timestamp, get_result_text(action.result)])

    if len(action_data) > 1:
        story.append(Paragraph('Actions', l0))
        action_table = platypus.Table(
            action_data,
            style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
        )
        story.append(action_table)

    check_data = [['Name', 'Timestamp', 'Result']]
    for check in step.prepared_criteria:
        name = f'{check.comparison.name} - {check.identifier}'
        timestamp = check.result.timestamp.ctime()
        check_data.append([name, timestamp, get_result_text(check.result)])

    if len(check_data) > 1:
        story.append(Paragraph('Checks', l0))
        check_table = platypus.Table(
            check_data,
            style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
        )
        story.append(check_table)


def build_comparison_page(
    story: List[Flowable],
    comp: Union[PreparedSignalComparison, PreparedToolComparison]
) -> None:
    """
    Build a page that summarizes a comparison and its result.
    Modifies the story in place, appending flowables to the end of the ``story``

    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    comp : Union[PreparedSignalComparison, PreparedToolComparison]
        The comparison to build a page for.
    """
    result = getattr(comp, 'result', None)
    if result:
        story.append(get_result_text(result))

    build_comparison_summary(story, comp)

    # settings table
    origin = comp.comparison
    build_settings_table(
        story, origin,
        omit_keys=['name', 'description', 'by_pv', 'by_attr', 'shared', 'configs']
    )
    # data table
    build_data_table(story, comp)


def build_comparison_summary(
    story: List[Flowable],
    comp: PreparedSignalComparison
) -> None:
    """
    Builds a big visual representation of the comparison, with a bounding box
    colored according to the Result severity.
    Modifies the story in place, appending flowables to the end of the ``story``

    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    comp : PreparedSignalComparison
        The comparison to build a summary for.
    """
    origin = comp.comparison
    try:
        symbol = symbol_map[type(origin)]
    except KeyError:
        logger.debug('unsupported comparison type')
        return
    comp_items = [['X', symbol]]
    if isinstance(origin, (Equals, NotEquals)):
        # tolerance format
        value = origin.value
        comp_items[0].append(value)
        if origin.atol and origin.rtol:
            tol = origin.atol + (origin.rtol * value)
            comp_items[0].append(f'± {tol:.3g}')
    elif isinstance(origin, (Greater, GreaterOrEqual, Less, LessOrEqual)):
        # no tolerance
        value = origin.value
        comp_items[0].append(value)
    elif isinstance(origin, Range):
        comp_items[0].insert(0, '≤')
        comp_items[0].insert(0, origin.low)
        comp_items[0].append(origin.high)

    color = RESULT_COLOR[comp.result.severity]
    comp_table = platypus.Table(
        comp_items,
        style=[('SIZE', (0, 0), (-1, -1), 40),
               ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
               ('BOX', (0, 0), (-1, -1), 2, color),
               ('BOTTOMPADDING', (0, 0), (-1, -1), 50)]
    )
    story.append(comp_table)


def build_data_table(
    story: List[Flowable],
    comp: PreparedComparison
) -> None:
    """
    Builds a table describing the data observed at the time the checkout was run.
    While ``comp`` is expected to be a ``PreparedComparison``, it is reductively
    a dataclass that can hold a DataCache instance.
    Modifies the story in place, appending flowables to the end of the ``story``.

    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    comp : PreparedComparison
        A comparison holding a DataCache instance
    """
    story.append(Paragraph('Observed Data', l0))
    # use cached value.
    observed_value = getattr(comp, 'data', 'N/A')
    if observed_value is None:
        # attr can be set to None
        observed_value = 'N/A'
    observed_value = Paragraph(str(observed_value), styles['BodyText'])
    try:
        timestamp = datetime.fromtimestamp(comp.signal.timestamp).ctime()
    except AttributeError:
        timestamp = 'unknown'

    try:
        source = Paragraph(getattr(comp.signal, 'name', ''), styles['BodyText'])
    except AttributeError:
        source = 'undefined'
    observed_data = [['Observed Value', 'Timestamp', 'Source'],
                     [observed_value, timestamp, source]]

    observed_table = platypus.Table(
        observed_data,
        style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
    )

    story.append(observed_table)


def build_settings_table(
    story: List[Flowable],
    item: AnyDataclass,
    omit_keys: List[str]
) -> None:
    """
    Auto-generate the settings for a given item.  Simply lists the field name
    and value of all fields in ``item``, unless the field name is listed in
    ``omit_keys``
    Modifies the story in place, appending flowables to the end of the ``story``

    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    item : AnyDataclass
        The origin of a prepared configuration/group.
        For passive checkouts this can be either .comparison or .config
        For active checkouts this typically the .origin field
    omit_keys : List[str]
        Fields of ``item`` to ignore.  Specify a field here if it contains objects
        with excessively long reprs, or will be handled later in the report
    """
    # settings table
    story.append(Paragraph('Settings', l0))
    settings_data = []
    for field in fields(item):
        if field.name not in omit_keys:
            settings_data.append(
                [field.name,
                 Paragraph(str(getattr(item, field.name)), styles['BodyText'])]
            )
    settings_table = platypus.Table(
        settings_data,
        style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
    )
    story.append(settings_table)


def build_group_page(
    story: List[Flowable],
    item: Union[PreparedConfiguration, PreparedProcedureGroup],
    omit_keys: Optional[List[str]] = None
) -> None:
    """
    Build a group page's contents.  Groups are the parents of dataclasses that
    hold results, so we try to summarize them here.
    Modifies the story in place, appending flowables to the end of the ``story``

    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    item : Union[PreparedConfiguration, PreparedProcedureGroup]
        A prepared configuration or procedure group.
    omit_keys : Optional[List[str]], optional
        Fields of ``item`` to ignore.  Specify a field here if it contains objects
        with excessively long reprs, or will be handled later in the report.
        In this case should include the sub-steps or sub-comparisons of ``item``,
        since they will be summarized in the results table.
        By default None.

    Raises
    ------
    TypeError
        If ``item`` is not recognized as a group
    """
    if isinstance(item, PreparedConfiguration):
        origin = item.config
        sub_steps = ['comparisons']
    elif isinstance(item, PreparedProcedureGroup):
        origin = item.origin
        sub_steps = ['steps']
    else:
        raise TypeError(f'Step type ({type(item)}) not recognized as a group')
    # build settings table
    build_settings_table(story, origin, omit_keys=omit_keys or [])
    build_results_table(story, item, list_names=sub_steps)


def build_results_table(
    story: List[Flowable],
    item: AnyDataclass,
    attr_names: Optional[List[str]] = None,
    list_names: Optional[List[str]] = None,
) -> None:
    """
    Builds a table with the results contained in this ``item``.
    If attr_names is provided, it is assumed that each string in ``attr_names``
    corresponds to a field holding a relevant Result.
    If list_names is provided, it is assumed that each string in ``list_names``
    corresponds to a field holding a list of other dataclasses, that in turn
    hold a Result.
    All of the aforementioned Results will be included in the table.

    Modifies the story in place, appending flowables to the end of the ``story``


    Parameters
    ----------
    story : List[Flowable]
        List of story objects that compose a reportlab PDF
    item : AnyDataclass
        A dataclass that involves Results
    list_names : Optional[List[str]], optional
        The names of fields that hold lists of dataclasses that in turn hold Results,
        by default None
    attr_names : Optional[List[str]], optional
        The names of fields that hold Results, by default None
    """
    results_data = []
    # grab results stored in specified attributes
    for attr in attr_names or []:
        result: Result = getattr(item, attr)
        timestamp = result.timestamp.ctime()
        results_data.append([attr.replace('_', ' '), timestamp,
                             get_result_text(result)])

    # grab results stored in attributes with lists of result-holding objects
    for list_attr in list_names or []:
        for list_item in getattr(item, list_attr, []):
            if isinstance(list_item, PreparedComparison):
                result_name = f'{list_item.comparison.name} - {list_item.identifier}'
            else:
                # e.g. PreparedValueToSignal
                result_name = list_item.name
            timestamp = list_item.result.timestamp.ctime()
            results_data.append([result_name, timestamp,
                                 get_result_text(list_item.result)])

    # make results a table
    if results_data:
        story.append(Paragraph('Results', l0))
        results_table = platypus.Table(
            results_data,
            style=[('GRID', (0, 0), (-1, -1), 1, colors.black)]
        )
        story.append(results_table)


class AtefReport(BaseDocTemplate):
    """
    Document template to auto-gather table of contents and manage styles
    Also holds config as an attempt to consolidate information, settings

    Should be initialized with the report file path and configuration file
    to render.

    .. code-block:: python

        doc = AtefReport('/path/to/pdf/location/report.pdf', config=file)

    Extra information can be passed at init or configured prior to file
    creation

    .. code-block:: python

        doc.set_info(author='JoeShmo', version='v1.0.1')
        doc.create_report()

    """
    LOGO = platypus.Image(Path(__file__).parent / 'assets' / 'SLAC_red.jpeg',
                          kind='proportional', width=6.0*units.inch,
                          height=6.0*units.inch)

    def __init__(
        self,
        filename: str,
        config: Union[PreparedFile, PreparedProcedureFile],
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
        self.header_center_text = ''
        self.footer_center_text = ''
        self.approval_slots = 1
        # for tracking untitled steps
        self._text_called_count = defaultdict(lambda: 0)

    def afterFlowable(self, flowable: Flowable) -> None:
        """
        Registers TOC entries. Automatically notifies TOC if the style
        name is recognized
        """
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
        """ Populate and build header table """
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
                ('GRID', (0, 0), (1, 2), 1, colors.grey),
                ('ALIGN', (0, 0), (0, 3), 'CENTER'),
                ('VALIGN', (0, 0), (3, 2), 'MIDDLE'),
                ('ALIGN', (2, 0), (3, 2), 'RIGHT'),
                ('SPAN', (0, 0), (0, 2)),
                ('SPAN', (1, 0), (1, 2)),
                ('BOX', (2, 0), (-1, -1), 1, colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.grey)
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
        """ Populate and build footer table """
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
                ('GRID', (0, 0), (2, 0), 1, colors.grey),
                ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('ALIGN', (0, 0), (3, 0), 'CENTER'),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.grey)
            ]
        )

        w, h = table.wrap(self.width, self.bottomMargin)
        table.drawOn(canvas, self.leftMargin - 22.4, h)
        canvas.restoreState()

    def set_info(
        self,
        author: Optional[str] = None,
        version: Optional[str] = None,
        header_text: Optional[str] = None,
        footer_text: Optional[str] = None,
        approval_slots: Optional[int] = None
    ) -> None:
        """ Ovderride or set default information used in the report """
        if author:
            self.author = author
        if version:
            self.verion = version
        if header_text:
            self.header_center_text = header_text
        if footer_text:
            self.footer_center_text = footer_text
        if approval_slots:
            self.approval_slots = approval_slots

    def get_info(self) -> Dict[str, Any]:
        return {
            'author': self.author,
            'version': self.version,
            'header_text': self.header_center_text,
            'footer_text': self.footer_center_text,
            'approval_slots': self.approval_slots
        }

    def build_cover_page(self, story: List[Flowable]) -> None:
        """
        Build the cover page and set up table of contents

        Parameters
        ----------
        story : List[Flowable]
            a list of components used to render the report.  New items
            are appended to this directly
        """
        toc = TableOfContents(dotsMinLevel=0)
        # For conciseness we use the same styles for headings and TOC entries
        toc.levelStyles = [PS('toc1', fontSize=12), PS('toc2', fontSize=10)]
        cover_style = PS('cover_title', fontSize=20, leading=22,
                         alignment=enums.TA_CENTER)
        story.append(platypus.NextPageTemplate('cover'))
        story.append(Paragraph('Checkout Report', cover_style))
        story.append(platypus.Spacer(width=0, height=.5*cm))
        story.append(self.LOGO)
        story.append(platypus.Spacer(width=0, height=.5*cm))
        story.append(Paragraph('Document Approval', l0))
        table_data = [
            ['Name:', 'Role:', 'Signature:', 'Date Approved:'],
        ]
        heights = [1*cm]
        for _ in range(self.approval_slots):
            table_data.append(['', '', '', ''])
            heights.append(1.5*cm)
        # Key, start(C,R), end(C,R), Setting
        approval_table = platypus.Table(
            table_data,
            colWidths=[3.5*cm, 5*cm, 5*cm, 3*cm],
            rowHeights=heights,
            style=[
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('ALIGN', (0, 0), (3, 0), 'CENTER'),
            ]
        )
        if self.approval_slots > 0:
            story.append(approval_table)
        story.append(platypus.NextPageTemplate('normal'))
        story.append(platypus.PageBreak())
        story.append(Paragraph('Table of Contents', cover_style))
        story.append(toc)
        story.append(platypus.PageBreak())

    def build_linked_header(self, text: str, style: PS) -> Paragraph:
        """ Create a unique bookmark name and add it to the header """
        mark_name = (text+style.name).encode('utf-8')
        bookmark_name = hashlib.sha1(mark_name).hexdigest()
        header = Paragraph(text + f'<a name="{bookmark_name}"/>', style)
        header._bookmark_name = bookmark_name
        return header

    def num_times_called(self, text: str) -> int:
        """
        Returns the number of times this method has been called with ``text``

        Parameters
        ----------
        text : str
            string to track

        Returns
        -------
        int
            number of times ``text`` has been called in this method
        """
        self._text_called_count[text] += 1

        return self._text_called_count[text]

    def build_header_with_default(
        self,
        story: List[Flowable],
        config: AnyDataclass,
        field: str,
        default_header: Optional[str] = None,
        style: PS = h2
    ) -> None:
        """
        Currently the top-level header-building helper method.

        Build a linked header with the attribute from the config if possible
        create a placeholder title otherwise. Link it at the end

        Parameters
        ----------
        story : List[Flowable]
            a list of components used to render the report.  New items
            are appended to this directly
        config : AnyDataclass
            an atef dataclass with attribute ``field``
        field : str
            name of field in ``config`` that holds the name to be used in this header
            can be a dotted attribute (e.g. "origin.name" etc)
        default_header : Optional[str]
            default header text, will be used if ``field`` cannot be found on ``config``
        style : PS
            style to apply to the header Paragraph flowable
        """
        try:
            name = attrgetter(field)(config)
        except AttributeError:
            name = None

        if not name and default_header:
            header_text = default_header
        if not name:
            header_text = f'Untitled {type(config).__name__} '
            header_text += f'#{self.num_times_called(header_text)}'
        else:
            header_text = name

        story.append(self.build_linked_header(header_text, style=style))

    def create_report(self) -> None:
        """ Build the final report. """
        raise NotImplementedError()


class PassiveAtefReport(AtefReport):
    """
    Report for Passive Checkouts.  Assumes specific PreparedFile structure
    """ + AtefReport.__doc__

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # customize some fields based on passive file
        self.version = self.config.file.version or self.version
        self.header_center_text = (self.config.root.config.name
                                   or self.header_center_text)
        self.footer_center_text = 'Passive Checkout Report'

    def create_report(self):
        """
        Top-level method for creating the final report.
        Use the stored config and calls various helpers
        """
        # Build story as a list of Flowable objects
        story = []
        # Cover page
        self.build_cover_page(story)
        # Results summary page
        self.build_summary(story)
        # Individual config / comparison pages
        for c, _ in walk_config_file(self.config.root):
            self.build_config_page(story, c)

        # must build several times to place items then gather info for ToC
        self.multiBuild(story)

    def build_summary(self, story: List[Flowable]):
        """
        Build summary table for checkout

        Parameters
        ----------
        story : List[Flowable]
            a list of components used to render the report.  New items
            are appended to this directly
        """
        story.append(self.build_linked_header('Checkout Summary', h1))
        story.append(platypus.Spacer(width=0, height=.5*cm))
        # table with results
        build_passive_summary_table(story, self.config)

    def build_config_page(
        self,
        story: List[Flowable],
        config: Union[PreparedConfiguration, PreparedComparison]
    ) -> None:
        """
        Build a config/comparison page.  Comprised of:
        - a basic header and overall result
        - Table of settings for this comparison or config
        - Table of observed data (if applicable / accessible)

        Parameters
        ----------
        story : List[Flowable]
            a list of components used to render the report.  New items
            are appended to this directly
        config : Union[PreparedConfiguration, PreparedComparison]
            the configuration or comparison dataclass
        """
        # Build default page, settings (and maybe data) table
        if isinstance(config, (PreparedGroup, PreparedDeviceConfiguration,
                      PreparedPVConfiguration, PreparedToolConfiguration)):
            self.build_header_with_default(story, config, 'config.name', style=h2)
            story.append(Paragraph(config.config.description or ''))
            omit_keys = ['name', 'description', 'by_pv', 'by_attr', 'shared', 'configs']
            build_group_page(story, config, omit_keys)
        elif isinstance(config, (PreparedSignalComparison, PreparedToolComparison)):
            header_text = config.comparison.name or ''
            header_text += f' - {config.identifier or ""}'
            story.append(self.build_linked_header(header_text, style=h2))
            story.append(Paragraph(config.comparison.description or ''))
            build_comparison_page(story, config)
        else:
            config_type = str(type(config).__name__)
            header = self.build_linked_header(config_type, h1)
            story.append(header)
            story.append(Paragraph('page format not found'))

        # end of section
        story.append(platypus.PageBreak())


class ActiveAtefReport(AtefReport):
    """
    Report for Active Checkouts.
    Assumes specifically the PreparedProcedureFile structure
    """ + AtefReport.__doc__

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # customize some fields based on passive file
        self.version = self.config.file.version or self.version
        self.header_center_text = (self.config.root.origin.name
                                   or self.header_center_text)
        self.footer_center_text = 'Active Checkout Report'

    def create_report(self) -> None:
        """
        Top-level method for creating the final report.
        Uses the stored config and calls various helpers
        """
        story = []
        self.build_cover_page(story)

        self.build_summary(story)

        for step, _ in walk_procedure_file(self.config.root):
            self.build_step_page(story, step)

        # multi-build to gather items for ToC
        self.multiBuild(story)

    def build_summary(self, story: List[Flowable]):
        """
        Build summary table for checkout

        Parameters
        ----------
        story : List[Flowable]
            a list of components used to render the report.  New items
            are appended to this directly
        """
        story.append(self.build_linked_header('Checkout Summary', h1))
        story.append(platypus.Spacer(width=0, height=.5*cm))
        # table with results
        lines = walk_procedure_file(self.config.root)
        table_data = [['Step Name', 'Result']]
        style = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
                 ('ALIGN', (0, 0), (1, 0), 'CENTER'),
                 ('BOX', (0, 0), (-1, -1), 1, colors.black),
                 ('BOX', (0, 0), (0, -1), 1, colors.black)]

        for i, (item, level) in enumerate(lines):
            # content
            prefix = '    ' * level
            if isinstance(item, PreparedProcedureStep):
                name = item.origin.name
            elif isinstance(item, PreparedComparison):
                name = str(item.comparison.name) + ' - ' + str(item.identifier)
            name = name or type(item).__name__
            table_data.append(
                [
                    prefix + f'{name}',
                    get_result_text(item.result)
                ]
            )

            # style
            if isinstance(item, PreparedProcedureGroup):
                style.append(['LINEABOVE', (0, i+1), (-1, i+1), 1, colors.black])
            else:
                style.append(['LINEABOVE', (0, i+1), (-1, i+1), 1, colors.lightgrey])

        table = platypus.Table(
            table_data, style=style
        )

        story.append(table)
        story.append(platypus.PageBreak())

    def build_step_page(
        self,
        story: List[Flowable],
        step: Union[PreparedProcedureStep, PreparedComparison]
    ) -> None:
        omit_keys = ['name', 'description', 'actions', 'steps', 'success_criteria']
        result_attrs = ['step_result', 'verify_result', 'result']
        if isinstance(step, PreparedProcedureGroup):
            self.build_header_with_default(story, step, 'origin.name', style=h1)
            story.append(Paragraph(step.origin.description or ''))
            build_group_page(story, step, omit_keys=omit_keys)
        elif isinstance(step, PreparedSetValueStep):
            self.build_header_with_default(story, step, 'origin.name', style=h2)
            story.append(Paragraph(step.origin.description or ''))
            build_settings_table(story, step.origin, omit_keys=omit_keys)
            build_action_check_table(story, step)
            build_results_table(story, step, attr_names=result_attrs,
                                list_names=['prepared_criteria'])
        elif isinstance(step, PreparedPassiveStep):
            self.build_header_with_default(story, step, 'origin.name', style=h2)
            story.append(Paragraph(step.origin.description or ''))
            build_settings_table(story, step.origin, omit_keys=omit_keys)
            story.append(Paragraph('Passive Checkout Results', l0))
            build_passive_summary_table(story, step.prepared_passive_file)
            build_results_table(story, step, attr_names=result_attrs)
        elif isinstance(step, PreparedProcedureStep):
            self.build_header_with_default(story, step, 'origin.name', style=h2)
            story.append(Paragraph(step.origin.description or ''))
            build_settings_table(story, step.origin, omit_keys=omit_keys)
            build_results_table(story, step, attr_names=result_attrs)
        elif isinstance(step, PreparedComparison):
            header_text = step.comparison.name or ''
            header_text += f' - {step.identifier or ""}'
            story.append(self.build_linked_header(header_text, style=h2))
            story.append(Paragraph(step.comparison.description or ''))
            build_comparison_page(story, step)
        else:
            self.build_header_with_default(story, step, 'origin.name', style=h1)
            story.append(Paragraph('page format not found'))

        # end of section
        story.append(platypus.PageBreak())
