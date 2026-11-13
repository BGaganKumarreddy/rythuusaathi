import React, { useState, useEffect } from 'react';
import {
  UserCheck,
  Sprout,
  MapPin,
  Layers,
  Droplets,
  Save,
  CheckCircle2,
  Shield,
  HelpCircle,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useConfig } from '../../context/ConfigContext';
import { useToast } from '../../context/ToastContext';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Input, Select } from '../../components/ui/Input';
import { Badge } from '../../components/ui/Badge';

const STORAGE_KEY = 'rythusaathi_farmer_profile';

export const FarmProfilePage = () => {
  const { user } = useAuth();
  const { crops, locations } = useConfig();
  const toast = useToast();

  const [farmerName, setFarmerName] = useState('');
  const [selectedState, setSelectedState] = useState('location:state:in-tg');
  const [selectedDistrict, setSelectedDistrict] = useState('location:district:in-tg-adilabad');
  const [primaryCrop, setPrimaryCrop] = useState('crop:oryza-sativa');
  const [farmSizeAcres, setFarmSizeAcres] = useState('4.5');
  const [soilType, setSoilType] = useState('black_cotton');
  const [irrigationSource, setIrrigationSource] = useState('borewell');
  const [isSaved, setIsSaved] = useState(false);

  // Load saved profile if available
  useEffect(() => {
    try {
      const saved = localStorage.getItem(`${STORAGE_KEY}_${user?.user_id || 'default'}`);
      if (saved) {
        const data = JSON.parse(saved);
        if (data.farmerName) setFarmerName(data.farmerName);
        if (data.selectedState) setSelectedState(data.selectedState);
        if (data.selectedDistrict) setSelectedDistrict(data.selectedDistrict);
        if (data.primaryCrop) setPrimaryCrop(data.primaryCrop);
        if (data.farmSizeAcres) setFarmSizeAcres(data.farmSizeAcres);
        if (data.soilType) setSoilType(data.soilType);
        if (data.irrigationSource) setIrrigationSource(data.irrigationSource);
      } else if (user?.user_id) {
        setFarmerName(user.user_id.split('-')[0] || 'Farmer');
      }
    } catch {
      // ignore JSON parse errors
    }
  }, [user]);

  // Hierarchical location filtering
  const states = locations.filter((l) => l.level === 'state');
  const districts = locations.filter(
    (l) => l.level === 'district' && (!selectedState || l.parent_id === selectedState)
  );

  const handleSave = (e) => {
    e.preventDefault();
    const profile = {
      farmerName,
      selectedState,
      selectedDistrict,
      primaryCrop,
      farmSizeAcres,
      soilType,
      irrigationSource,
      updatedAt: new Date().toISOString(),
    };
    localStorage.setItem(`${STORAGE_KEY}_${user?.user_id || 'default'}`, JSON.stringify(profile));
    setIsSaved(true);
    toast.success('Farm profile saved successfully!', 'Profile Updated');
    setTimeout(() => setIsSaved(false), 3000);
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Badge variant="active" size="sm">Farmer First</Badge>
          <span className="text-xs text-[#78837B]">• Personalized Agronomic Context</span>
        </div>
        <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
          Farm Profile & Field Parameters
        </h1>
        <p className="text-sm text-[#78837B] mt-1">
          Keep your default farm location and soil attributes up-to-date for instant advisory grounding.
        </p>
      </div>

      <form onSubmit={handleSave} className="space-y-6">
        <Card>
          <div className="space-y-6">
            <h2 className="text-base font-bold text-[#202B24] flex items-center gap-2 border-b border-[#E6EAE4] pb-3">
              <UserCheck className="w-5 h-5 text-[#347A50]" />
              <span>Farmer & Holding Details</span>
            </h2>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Input
                label="Farmer Name / Preferred Display Name"
                id="farmerName"
                value={farmerName}
                onChange={(e) => setFarmerName(e.target.value)}
                placeholder="e.g. Ramesh Reddy"
                helperText="Used when generating personalized advisory reports"
              />

              <Input
                label="Total Farm Landholding (Acres)"
                id="farmSize"
                type="number"
                step="0.1"
                min="0.1"
                value={farmSizeAcres}
                onChange={(e) => setFarmSizeAcres(e.target.value)}
                placeholder="e.g. 5"
                helperText="Helps calculate input and subsidy eligibility tiers"
              />
            </div>
          </div>
        </Card>

        <Card>
          <div className="space-y-6">
            <h2 className="text-base font-bold text-[#202B24] flex items-center gap-2 border-b border-[#E6EAE4] pb-3">
              <MapPin className="w-5 h-5 text-[#347A50]" />
              <span>Geographic Hierarchy</span>
            </h2>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Select
                label="State"
                id="state"
                value={selectedState}
                onChange={(e) => {
                  setSelectedState(e.target.value);
                  const matchingDistricts = locations.filter(
                    (l) => l.level === 'district' && l.parent_id === e.target.value
                  );
                  if (matchingDistricts.length > 0) {
                    setSelectedDistrict(matchingDistricts[0].location_id);
                  } else {
                    setSelectedDistrict('');
                  }
                }}
                placeholder="Select State"
                options={states.map((s) => ({
                  value: s.location_id,
                  label: s.display_name,
                }))}
              />

              <Select
                label="District"
                id="district"
                value={selectedDistrict}
                onChange={(e) => setSelectedDistrict(e.target.value)}
                placeholder="Select District"
                disabled={districts.length === 0}
                options={districts.map((d) => ({
                  value: d.location_id,
                  label: d.display_name,
                }))}
              />
            </div>
          </div>
        </Card>

        <Card>
          <div className="space-y-6">
            <h2 className="text-base font-bold text-[#202B24] flex items-center gap-2 border-b border-[#E6EAE4] pb-3">
              <Sprout className="w-5 h-5 text-[#347A50]" />
              <span>Agronomic Characteristics</span>
            </h2>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <Select
                label="Primary Monitored Crop"
                id="primaryCrop"
                value={primaryCrop}
                onChange={(e) => setPrimaryCrop(e.target.value)}
                placeholder="Select Crop"
                options={crops.map((c) => ({
                  value: c.crop_id,
                  label: `${c.common_names?.[0]} (${c.scientific_name})`,
                }))}
              />

              <Select
                label="Soil Classification"
                id="soilType"
                value={soilType}
                onChange={(e) => setSoilType(e.target.value)}
                placeholder="Select Soil"
                options={[
                  { value: 'black_cotton', label: 'Black Cotton Soil (Regur)' },
                  { value: 'alluvial', label: 'Alluvial Loam' },
                  { value: 'red_sandy', label: 'Red Sandy Loam' },
                  { value: 'clay_loam', label: 'Clay Loam' },
                ]}
              />

              <Select
                label="Primary Irrigation Source"
                id="irrigation"
                value={irrigationSource}
                onChange={(e) => setIrrigationSource(e.target.value)}
                placeholder="Select Irrigation"
                options={[
                  { value: 'borewell', label: 'Borewell / Tube Well' },
                  { value: 'canal', label: 'Canal Irrigation' },
                  { value: 'drip', label: 'Drip / Micro-irrigation' },
                  { value: 'rainfed', label: 'Rainfed (Kharif Monsoon)' },
                ]}
              />
            </div>
          </div>
        </Card>

        <div className="flex items-center justify-between pt-2">
          <div className="flex items-center gap-2 text-xs text-[#78837B]">
            <Shield className="w-4 h-4 text-[#347A50]" />
            <span>Profile stored locally per tenant session. Never shared with unapproved third parties.</span>
          </div>

          <Button type="submit" variant="primary" icon={Save}>
            {isSaved ? 'Saved!' : 'Save Farm Profile'}
          </Button>
        </div>
      </form>
    </div>
  );
};

