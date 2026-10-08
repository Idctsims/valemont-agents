export function PageHeader({
  label,
  title,
  summary,
  children,
}: {
  label: string;
  title: string;
  summary?: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="mb-8 lg:mb-12">
      <p className="label-mono text-text-muted">{label}</p>
      <h1 className="mt-3 font-display text-4xl text-text text-balance lg:text-display">{title}</h1>
      {summary && (
        <p className="mt-4 max-w-measure text-lg text-text-muted text-pretty">{summary}</p>
      )}
      {children}
    </header>
  );
}
