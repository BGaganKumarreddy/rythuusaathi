import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  FileText,
  Filter,
  Search,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  AlertTriangle,
  ArrowRight,
  Code,
  Calendar,
  Layers,
} from 'lucide-react';
import { historyService } from '../../services/historyService';
import { advisoryService } from '../../services/advisoryService';
import { useConfig } from '../../context/ConfigContext';
import { useToast } from '../../context/ToastContext';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { Modal } from '../../components/ui/Modal';
import { Select } from '../../components/ui/Input';
import { EmptyState, CardSkeleton } from '../../components/ui/Feedback';

export const HistoryPage = () => {
  const { crops } = useConfig();
  const toast = useToast();

  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [limit] = useState(10);
  const [offset, setOffset] = useState(0);
  const [selectedCrop, setSelectedCrop] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  // Trace Modal state
  const [selectedTraceId, setSelectedTraceId] = useState(null);
  const [traceData, setTraceData] = useState(null);
  const [traceLoading, setTraceLoading] = useState(false);

  const loadHistory = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await historyService.getHistory({
        limit,
        offset,
        crop: selectedCrop || undefined,
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      toast.error('Failed to load past advisory consultations');
    } finally {
      setIsLoading(false);
    }
  }, [limit, offset, selectedCrop, toast]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const handleOpenTrace = async (e, advisoryId) => {
    e.preventDefault();
    e.stopPropagation();
    setSelectedTraceId(advisoryId);
    setTraceLoading(true);
    try {
      const t = await advisoryService.getAdvisoryTrace(advisoryId);
      setTraceData(t);
    } catch {
      toast.error('Diagnostic trace unavailable for this consultation');
    } finally {
      setTraceLoading(false);
    }
  };

  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit) || 1;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-[#E6EAE4]">
        <div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Consultation History & Archives
          </h1>
          <p className="text-sm text-[#78837B] mt-0.5">
            Durable SQLite WAL historical records with tenant-level owner isolation
          </p>
        </div>

        <Link to="/advisories/new">
          <Button variant="primary" size="md" icon={Sparkles}>
            New Advisory
          </Button>
        </Link>
      </div>

      {/* Filter Toolbar */}
      <Card padding="p-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3 w-full sm:w-72">
            <Select
              placeholder="All Crops"
              value={selectedCrop}
              onChange={(e) => {
                setSelectedCrop(e.target.value);
                setOffset(0);
              }}
              options={crops.map((c) => ({
                value: c.common_names?.[0]?.toLowerCase() || c.crop_id,
                label: c.common_names?.[0] || c.crop_id,
              }))}
            />
          </div>

          <div className="text-xs text-[#78837B] flex items-center gap-2">
            <span>Showing <strong>{items.length}</strong> of <strong>{total}</strong> consultations</span>
            {selectedCrop && (
              <button
                onClick={() => {
                  setSelectedCrop('');
                  setOffset(0);
                }}
                className="text-[#347A50] hover:underline font-medium"
              >
                Clear filter
              </button>
            )}
          </div>
        </div>
      </Card>

      {/* History List */}
      {isLoading ? (
        <div className="space-y-4">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No Advisory Consultations Found"
          description={
            selectedCrop
              ? `No consultations found for "${selectedCrop}". Try selecting All Crops.`
              : 'You have not submitted any advisory inquiries yet.'
          }
          action={
            <Link to="/advisories/new">
              <Button variant="primary" size="sm" icon={Sparkles}>
                Create Your First Advisory
              </Button>
            </Link>
          }
        />
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <Link
              key={item.advisory_id}
              to={`/advisories/${encodeURIComponent(item.advisory_id)}`}
              className="block group"
            >
              <Card
                padding="p-5"
                hover
                className="group-hover:border-[#347A50]/50 transition-all"
              >
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="font-bold text-base text-[#202B24] truncate group-hover:text-[#183D2B] transition-colors">
                      {item.query}
                    </span>
                    <Badge variant={item.status?.toLowerCase()} size="sm">
                      {item.status}
                    </Badge>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      type="button"
                      onClick={(e) => handleOpenTrace(e, item.advisory_id)}
                      className="p-1.5 rounded-lg border border-[#E6EAE4] hover:bg-[#F7F8F4] text-[#78837B] hover:text-[#202B24] text-xs flex items-center gap-1 transition-colors"
                      title="Inspect Diagnostic Trace"
                    >
                      <Code className="w-3.5 h-3.5" />
                      <span className="hidden sm:inline">Trace</span>
                    </button>
                    <div className="w-7 h-7 rounded-lg bg-[#F7F8F4] flex items-center justify-center text-[#78837B] group-hover:bg-[#DCE9DC] group-hover:text-[#183D2B] transition-colors">
                      <ArrowRight className="w-4 h-4" />
                    </div>
                  </div>
                </div>

                {/* Synthesis preview */}
                <p className="text-xs text-[#78837B] line-clamp-2 leading-relaxed mb-3">
                  {item.synthesis_preview || 'Click to view full recommendations and agent evidence.'}
                </p>

                {/* Metadata tags */}
                <div className="flex flex-wrap items-center gap-3 text-xs text-[#78837B] pt-2 border-t border-[#E6EAE4]/60">
                  {item.crop && (
                    <span className="bg-[#DCE9DC] text-[#183D2B] font-semibold px-2 py-0.5 rounded-md text-[11px]">
                      {item.crop}
                    </span>
                  )}
                  {item.location && <span>Location: {item.location}</span>}
                  {item.growth_stage && <span>Stage: {item.growth_stage}</span>}
                  <span>Mode: <strong>{item.execution_mode}</strong></span>
                  <span>•</span>
                  <span>{new Date(item.created_at).toLocaleDateString()}</span>
                  {item.active_alert_count > 0 && (
                    <span className="text-amber-700 font-semibold flex items-center gap-1">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      {item.active_alert_count} active alert{item.active_alert_count > 1 ? 's' : ''}
                    </span>
                  )}
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}

      {/* Pagination Controls */}
      {total > limit && (
        <div className="flex items-center justify-between p-4 bg-white rounded-2xl border border-[#E6EAE4]">
          <span className="text-xs text-[#78837B]">
            Page {currentPage} of {totalPages}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
              icon={ChevronLeft}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + limit >= total}
              onClick={() => setOffset(offset + limit)}
            >
              <span>Next</span>
              <ChevronRight className="w-4 h-4 ml-1" />
            </Button>
          </div>
        </div>
      )}

      {/* Diagnostic Trace Modal */}
      <Modal
        isOpen={!!selectedTraceId}
        onClose={() => {
          setSelectedTraceId(null);
          setTraceData(null);
        }}
        title="Diagnostic Execution Trace"
        subtitle={`Advisory Consultation ID: ${selectedTraceId}`}
        maxWidth="max-w-2xl"
        footer={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setSelectedTraceId(null);
              setTraceData(null);
            }}
          >
            Close
          </Button>
        }
      >
        {traceLoading ? (
          <div className="py-8 text-center text-xs text-[#78837B]">
            Loading diagnostic execution trace...
          </div>
        ) : traceData ? (
          <pre className="p-3 bg-slate-900 text-emerald-400 rounded-xl overflow-x-auto text-[11px] leading-relaxed max-h-96">
            {JSON.stringify(traceData, null, 2)}
          </pre>
        ) : (
          <div className="text-xs text-red-600">Failed to load trace</div>
        )}
      </Modal>
    </div>
  );
};

