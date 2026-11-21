import { NavLink } from "react-router";

const tabClass = ({ isActive }: { isActive: boolean }) =>
  `-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
    isActive
      ? "border-teal-600 text-teal-700"
      : "border-transparent text-slate-500 hover:text-slate-700"
  }`;

export function AdminTabs() {
  return (
    <nav aria-label="Admin sections" className="flex gap-2 border-b border-slate-200">
      <NavLink to="/admin" end className={tabClass}>
        Documents
      </NavLink>
      <NavLink to="/admin/behaviour" className={tabClass}>
        Assistant behaviour
      </NavLink>
    </nav>
  );
}
