{{ fullname | escape | underline}}

.. currentmodule:: {{ module }}

.. autoclass:: {{ objname }}

    {% block methods %}
    {% if methods %}
    .. rubric:: Methods

    {% for item in methods %}
    .. automethod:: {{ item }}
    {%- endfor %}
    {% endif %}
    {% endblock %}

    {% block attributes %}
    {% if attributes %}
    .. rubric:: Attributes

    {% for item in attributes %}
    .. autoattribute:: {{ item }}
    {%- endfor %}
    {% endif %}
    {% endblock %}
