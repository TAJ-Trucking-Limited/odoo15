/** @odoo-module **/

import { registry } from "@web/core/registry";
import { isHtmlEmpty } from "@web/core/utils/html";
import { FormCompiler } from "@web/views/form/form_compiler";
import { FormRenderer } from "@web/views/form/form_renderer";
import { formView } from "@web/views/form/form_view";
import { toStringExpression } from "@web/views/utils";

// Future lifecycle timestamps are intentionally blank, not missing trip data.
const LIFECYCLE_STATES = {
    ready_at: ["ready", "in_progress", "completed"],
    started_at: ["in_progress", "completed"],
    completed_at: ["completed"],
    cancelled_at: ["cancelled"],
};

function pageFieldSpecs(page) {
    const specs = [];
    for (const field of page.querySelectorAll("field")) {
        const invisible = [];
        let node = field;
        while (node !== page) {
            // A list is one field; do not count the cells inside its rows.
            if (node !== field && ["field", "page"].includes(node.tagName)) {
                break;
            }
            if (node.hasAttribute("invisible")) {
                invisible.push(node.getAttribute("invisible"));
            }
            node = node.parentElement;
        }
        if (node === page) {
            specs.push({ name: field.getAttribute("name"), invisible });
        }
    }
    return specs;
}

class TripFormCompiler extends FormCompiler {
    compileNotebook(element, params) {
        // Capture modifiers before the normal compiler transforms the nodes.
        const pages = new Map(
            [...element.children]
                .filter((page) => page.tagName === "page" && page.hasAttribute("name"))
                .map((page) => [
                    toStringExpression(page.getAttribute("name")),
                    { name: page.getAttribute("name"), fields: pageFieldSpecs(page) },
                ])
        );
        const notebook = super.compileNotebook(element, params);
        for (const slot of notebook.children) {
            const page = pages.get(slot.getAttribute("name"));
            if (page) {
                slot.setAttribute(
                    "title",
                    `__comp__.tripTabTitle(${slot.getAttribute("title")}, ${JSON.stringify(page)})`
                );
            }
        }
        return notebook;
    }
}

class TripFormRenderer extends FormRenderer {
    tripTabTitle(title, page) {
        const record = this.props.record;
        const emptyFields = new Set();
        for (const spec of page.fields) {
            const field = record.fields[spec.name];
            if (!field || spec.invisible.some((expression) =>
                this.evaluateBooleanExpr(expression, record.evalContextWithVirtualIds)
            )) {
                continue;
            }
            if (
                page.name === "trip_lifecycle" &&
                LIFECYCLE_STATES[spec.name] &&
                !LIFECYCLE_STATES[spec.name].includes(record.data.state)
            ) {
                continue;
            }
            if (this.isTripFieldEmpty(field.type, record.data[spec.name])) {
                emptyFields.add(spec.name);
            }
        }
        return `${title} (${emptyFields.size})`;
    }

    isTripFieldEmpty(type, value) {
        // Zero and an unchecked checkbox are valid values, not empty fields.
        if (type === "boolean" || typeof value === "number") {
            return false;
        }
        if (value === false || value === null || value === undefined) {
            return true;
        }
        if (type === "one2many" || type === "many2many") {
            return value.count === 0;
        }
        if (type === "html") {
            return isHtmlEmpty(value);
        }
        return typeof value === "string" && value.trim() === "";
    }
}

registry.category("views").add("taj_transport_trip_form", {
    ...formView,
    Compiler: TripFormCompiler,
    Renderer: TripFormRenderer,
});
