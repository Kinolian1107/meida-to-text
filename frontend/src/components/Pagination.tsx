export default function Pagination({
  page,
  pageSize,
  total,
  onChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;

  return (
    <div className="pagination">
      <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        上一頁
      </button>
      <span className="muted">
        第 {page} / {pageCount} 頁（共 {total} 筆）
      </span>
      <button type="button" disabled={page >= pageCount} onClick={() => onChange(page + 1)}>
        下一頁
      </button>
    </div>
  );
}
