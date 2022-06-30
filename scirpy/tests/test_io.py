from anndata._core.anndata import AnnData
from scirpy.io import (
    read_10x_vdj,
    read_tracer,
    read_airr,
    read_bracer,
    from_airr_cells,
    to_airr_cells,
    to_dandelion,
    from_dandelion,
    read_bd_rhapsody,
    write_airr,
    upgrade_schema,
    AirrCell,
)
from scirpy.io._io import _infer_locus_from_gene_names, _cdr3_from_junction
from scirpy.io._util import _check_upgrade_schema
from scirpy.util import _is_na, _is_false
import scirpy as ir
import numpy as np
import pytest
import pandas.testing as pdt
from . import TESTDATA
from .util import _normalize_df_types
from functools import lru_cache
import scanpy as sc
import pandas as pd


@lru_cache(None)
def _read_anndata_from_10x_sample(path):
    """Read full 10x CSV table and convert it to IR objects, ready
    to be used for roundtrip conversions.

    Test-dataset from https://support.10xgenomics.com/single-cell-vdj/datasets/3.1.0/vdj_nextgem_hs_pbmc3
    and https://support.10xgenomics.com/single-cell-vdj/datasets/4.0.0/sc5p_v2_hs_melanoma_10k
    under CC-BY-4.0.

    Pytest only caches one fixture at a time, i.e. it doesn't work with parametrized
    fixtures. Therefore, we use the lru_cache instead.
    """
    print(f"Reading 10x file: {path}")
    anndata = read_10x_vdj(path, include_fields=None)
    return anndata


@pytest.fixture
def anndata_from_10x_sample(request):
    """Make a copy for each function. Using this construct saves time compared
    to reading in the 10x files for each request to the fixture"""
    return _read_anndata_from_10x_sample(request.param).copy()


@pytest.mark.parametrize(
    "junction_aa,junction_nt,cdr3_aa,cdr3_nt",
    [
        (
            "CQQYGSSLTWTF",
            "TGTCAGCAGTATGGTAGCTCACTTACGTGGACGTTC",
            "QQYGSSLTWT",
            "CAGCAGTATGGTAGCTCACTTACGTGGACG",
        ),
        ("CYSHSPTSMWVS", "TGCTACTCACATTCACCTACTAGCATGTGGGTGTCC", None, None),
        (None, None, None, None),
    ],
)
def test_cdr3_from_junction(junction_aa, junction_nt, cdr3_aa, cdr3_nt):
    assert _cdr3_from_junction(junction_aa, junction_nt) == (cdr3_aa, cdr3_nt)


@pytest.mark.parametrize(
    "anndata_from_10x_sample",
    [
        TESTDATA / "10x/filtered_contig_annotations.csv",
    ],
    indirect=True,
)
def test_upgrade_schema(anndata_from_10x_sample):
    adata = sc.read_h5ad(TESTDATA / "wu2020_200_old_schema.h5ad")
    upgrade_schema(adata)

    # should raise error if already upgraded
    with pytest.raises(ValueError):
        upgrade_schema(adata)

    with pytest.raises(ValueError):
        upgrade_schema(anndata_from_10x_sample)


def test_check_upgrade_schema():
    @_check_upgrade_schema()
    def dummy_fun(adata, foo, *, bar):
        assert adata.shape[0] > 0
        assert foo == "foo"
        assert bar == "bar"
        return True

    adata = sc.read_h5ad(TESTDATA / "wu2020_200_old_schema.h5ad")
    with pytest.raises(ValueError):
        dummy_fun(adata, "foo", bar="bar")

    upgrade_schema(adata)

    assert dummy_fun(adata, "foo", bar="bar") is True


def test_airr_cell():
    """Test that an AirrCell can be properly initialized, and cell attributes
    are stored and validated properly"""
    ac = AirrCell("cell1", cell_attribute_fields=("fieldA", "fieldB"))
    ac["fieldA"] = "a"
    ac["fieldC"] = "c"
    ac["fieldD"] = "d"
    assert ac["fieldD"] == "d"
    del ac["fieldD"]
    with pytest.raises(KeyError):
        ac["fieldD"]

    chain1 = AirrCell.empty_chain_dict()
    chain2 = AirrCell.empty_chain_dict()
    chain1["fieldA"] = "a"
    chain1["fieldB"] = "b"
    chain1["fieldC"] = "c"
    chain2["fieldA"] = "a"
    chain2["fieldB"] = "invalid"
    chain2["fieldC"] = "c"
    ac.add_chain(chain1)
    with pytest.raises(ValueError):
        ac.add_chain(chain2)

    assert ac["fieldB"] == "b"
    assert "fieldB" not in ac.chains[0]
    assert ac.chains[0]["fieldC"] == "c"


def test_airr_cell_empty():
    """An empty airr cell should still be convertable to an airr record or a scirpy
    record"""
    ac = AirrCell("cell1")
    airr_record = list(ac.to_airr_records())
    assert airr_record == []

    ac.to_scirpy_record()


@pytest.mark.parametrize(
    "chain_dict,expected",
    [
        (
            {
                "v_call": "nan",
                "d_call": "nan",
                "j_call": "nan",
                "c_call": "nan",
            },
            None,
        ),
        (
            {
                "v_call": "TRAV36/DV7*01",
                "d_call": "TRDD1*01",
                "j_call": "TRDJ2*01",
                "c_call": "TRDC",
            },
            "TRD",
        ),
        (
            {
                "v_call": "TRAV36/DV7*01",
                "d_call": "nan",
                "j_call": "TRAJ2*01",
                "c_call": "TRAC",
            },
            "TRA",
        ),
        (
            {
                "v_call": "TRAV12-1*01",
                "d_call": "nan",
                "j_call": "TRAJ23*01",
                "c_call": "TRAC",
            },
            "TRA",
        ),
        (
            {
                "v_call": "TRAV12-1*01",
                "d_call": "nan",
                "j_call": "TRBJ1-3*01",
                "c_call": "TRAC",
            },
            None,
        ),
    ],
)
def test_infer_locus_from_gene_name(chain_dict, expected):
    assert expected is _infer_locus_from_gene_names(chain_dict)


@pytest.mark.parametrize(
    "path",
    [
        TESTDATA / "BD/RhapBDFDemo_BCR_Dominant_Contigs.csv.gz",
        TESTDATA / "BD/RhapVDJDemo_BCR_Unfiltered_Contigs.csv.gz",
    ],
)
def test_read_and_convert_bd_samples(path):
    """Test that a full BD dataset can be imported without errors"""
    assert read_bd_rhapsody(path).shape[0] > 0


@pytest.mark.parametrize(
    "anndata_from_10x_sample",
    [
        TESTDATA / "10x/vdj_nextgem_hs_pbmc3_t_filtered_contig_annotations.csv.gz",
        TESTDATA / "10x/sc5p_v2_hs_melanoma_10k_b_filtered_contig_annotations.csv.gz",
        TESTDATA / "10x/filtered_contig_annotations.csv",
    ],
    indirect=True,
)
def test_read_and_convert_10x_example(anndata_from_10x_sample):
    """Test that a full 10x CSV table can be imported without errors.

    To this end, only the fixture needs to be loaded.
    """
    assert anndata_from_10x_sample.shape[0] > 0


@pytest.mark.parametrize(
    "anndata_from_10x_sample",
    [
        TESTDATA / "10x/vdj_nextgem_hs_pbmc3_t_filtered_contig_annotations.csv.gz",
        TESTDATA / "10x/sc5p_v2_hs_melanoma_10k_b_filtered_contig_annotations.csv.gz",
        TESTDATA / "10x/filtered_contig_annotations.csv",
    ],
    indirect=True,
)
def test_ir_objs_roundtrip_conversion(anndata_from_10x_sample):
    """Check that an anndata object can be converted to ir_objs and back
    without loss"""
    anndata = anndata_from_10x_sample
    ir_objs = to_airr_cells(anndata)
    anndata2 = from_airr_cells(ir_objs)
    _normalize_df_types(anndata.obs)
    _normalize_df_types(anndata2.obs)
    pdt.assert_frame_equal(
        anndata.obs, anndata2.obs, check_dtype=False, check_categorical=False
    )


@pytest.mark.parametrize(
    "anndata",
    [
        sc.AnnData(),
        sc.AnnData(obs=pd.DataFrame().assign(a=["a", "b", "c"], x=["x", "y", "z"])),
    ],
)
def test_ir_objs_roundtrip_conversion_no_ir(anndata):
    """Check that an anndata object that does not contain IR information
    can still be converted to ir_objs and back"""
    ir_objs = to_airr_cells(anndata)
    anndata2 = from_airr_cells(ir_objs)
    _normalize_df_types(anndata.obs)
    # these extra columns are expected.
    obs2 = anndata2.obs.drop(
        columns=["extra_chains", "has_ir", "multi_chain"], errors="ignore"
    )
    _normalize_df_types(obs2)
    pdt.assert_frame_equal(
        anndata.obs, obs2, check_dtype=False, check_categorical=False, check_names=False
    )


@pytest.mark.parametrize(
    "anndata_from_10x_sample",
    [
        TESTDATA / "10x/vdj_nextgem_hs_pbmc3_t_filtered_contig_annotations.csv.gz",
        TESTDATA / "10x/sc5p_v2_hs_melanoma_10k_b_filtered_contig_annotations.csv.gz",
        TESTDATA / "10x/filtered_contig_annotations.csv",
    ],
    indirect=True,
)
def test_airr_roundtrip_conversion(anndata_from_10x_sample, tmp_path):
    """Test that writing and reading to and from AIRR format results in the
    identity"""
    anndata = anndata_from_10x_sample
    tmp_file = tmp_path / "test.airr.tsv"
    write_airr(anndata, tmp_file)
    anndata2 = read_airr(tmp_file, include_fields=None)
    _normalize_df_types(anndata.obs)
    _normalize_df_types(anndata2.obs)
    pdt.assert_frame_equal(
        anndata.obs, anndata2.obs, check_dtype=False, check_categorical=False
    )


@pytest.mark.extra
@pytest.mark.parametrize(
    "anndata_from_10x_sample",
    [
        TESTDATA / "10x/sc5p_v2_hs_melanoma_10k_b_filtered_contig_annotations.csv.gz",
    ],
    indirect=True,
)
def test_convert_dandelion(anndata_from_10x_sample):
    """Test dandelion round-trip conversion"""
    anndata = anndata_from_10x_sample
    ddl = to_dandelion(anndata)
    anndata2 = from_dandelion(ddl)

    # dandelion reorders cell barcodes
    ir_objs1 = sorted(to_airr_cells(anndata), key=lambda x: x.cell_id)
    ir_objs2 = sorted(to_airr_cells(anndata2), key=lambda x: x.cell_id)

    assert len(ir_objs1) == len(ir_objs2) == anndata.shape[0]

    # Frame-level comparison is not possible as the "extra chains" field is different
    # due to 'sequence_id' being different.
    for ir_obj1, ir_obj2 in zip(ir_objs1, ir_objs2):
        assert len(ir_obj1.chains) == len(ir_obj2.chains)
        chains1 = sorted(ir_obj1.chains, key=AirrCell._key_sort_chains)
        chains2 = sorted(ir_obj2.chains, key=AirrCell._key_sort_chains)

        for tmp_chain1, tmp_chain2 in zip(chains1, chains2):
            # this field is expected to be different
            del tmp_chain1["sequence_id"]
            del tmp_chain2["sequence_id"]

            # remove extra cdr3 and cdr3_aa if exist
            _ = tmp_chain1.pop("cdr3", "Not exists")
            _ = tmp_chain1.pop("cdr3_aa", "Not exists")

            assert tmp_chain1 == tmp_chain2


@pytest.mark.conda
def test_read_10x_csv():
    anndata = read_10x_vdj(TESTDATA / "10x/filtered_contig_annotations.csv")
    obs = anndata.obs
    assert obs.shape[0] == 5
    cell1 = obs.iloc[1, :]
    cell2 = obs.iloc[3, :]
    cell3 = obs.iloc[4, :]

    assert cell1.name == "AAACCTGAGTACGCCC-1"
    assert cell1["IR_VDJ_1_junction_aa"] == "CASSLGPSTDTQYF"
    assert cell1["IR_VDJ_1_junction"] == "TGTGCCAGCAGCTTGGGACCTAGCACAGATACGCAGTATTTT"
    assert cell1["IR_VDJ_1_duplicate_count"] == 55
    assert cell1["IR_VDJ_1_consensus_count"] == 18021
    assert cell1["IR_VDJ_1_v_call"] == "TRBV7-2"
    assert cell1["IR_VDJ_1_d_call"] == "TRBD2"
    assert cell1["IR_VDJ_1_j_call"] == "TRBJ2-3"
    assert cell1["IR_VDJ_1_c_call"] == "TRBC2"
    assert _is_false(cell1["multi_chain"])
    assert cell1["IR_VJ_1_locus"] == "TRA"
    assert cell1["IR_VDJ_1_locus"] == "TRB"

    assert cell2.name == "AAACCTGGTCCGTTAA-1"
    assert cell2["IR_VJ_1_junction_aa"] == "CALNTGGFKTIF"
    assert cell2["IR_VJ_2_junction_aa"] == "CAVILDARLMF"
    assert cell2["IR_VJ_1_duplicate_count"] == 5
    assert cell2["IR_VJ_2_duplicate_count"] == 5
    assert cell2["IR_VJ_1_locus"] == "TRA"
    assert cell2["IR_VDJ_1_locus"] == "TRB"
    assert cell2["IR_VJ_2_locus"] == "TRA"
    assert _is_na(cell2["IR_VDJ_2_junction_aa"])

    assert cell3.name == "AAACTTGGTCCGTTAA-1"
    assert cell3["IR_VJ_1_locus"] == "IGK"
    assert cell3["IR_VDJ_1_locus"] == "IGH"


@pytest.mark.conda
def test_read_10x_csv_cr6():
    """Test additional cols from CR6 outputs: fwr{1,2,3,4}{,_nt} and cdr{1,2}{,_nt}"""

    anndata = read_10x_vdj(
        TESTDATA
        / "10x/10k_BMMNC_5pv2_nextgem_Multiplex_vdj_t_all_contig_annotations_small.csv",
        include_fields=None,
    )
    obs = anndata.obs
    assert obs.shape[0] == 2
    cell1 = obs.iloc[0, :]

    assert cell1.name == "AAACCTGCACAGGTTT-1"
    assert cell1["IR_VDJ_1_fwr1_aa"] == "KAGVTQTPRYLIKTRGQQVTLSCSPI"
    assert (
        cell1["IR_VDJ_1_fwr1"]
        == "AAGGCTGGAGTCACTCAAACTCCAAGATATCTGATCAAAACGAGAGGACAGCAAGTGACACTGAGCTGCTCCCCTATC"
    )
    assert cell1["IR_VDJ_1_cdr1_aa"] == "SGHRS"
    assert cell1["IR_VDJ_1_cdr1"] == "TCTGGGCATAGGAGT"
    assert cell1["IR_VDJ_1_fwr2_aa"] == "VSWYQQTPGQGLQFLFE"
    assert (
        cell1["IR_VDJ_1_fwr2"] == "GTATCCTGGTACCAACAGACCCCAGGACAGGGCCTTCAGTTCCTCTTTGAA"
    )
    assert cell1["IR_VDJ_1_cdr2_aa"] == "YFSETQ"
    assert cell1["IR_VDJ_1_cdr2"] == "TACTTCAGTGAGACACAG"
    assert cell1["IR_VDJ_1_fwr3_aa"] == "RNKGNFPGRFSGRQFSNSRSEMNVSTLELGDSALYL"
    assert (
        cell1["IR_VDJ_1_fwr3"]
        == "AGAAACAAAGGAAACTTCCCTGGTCGATTCTCAGGGCGCCAGTTCTCTAACTCTCGCTCTGAGATGAATGTGAGCACCTTGGAGCTGGGGGACTCGGCCCTTTATCTT"
    )
    assert cell1["IR_VDJ_1_cdr3_aa"] == "ASSWMDRGEAF"
    assert cell1["IR_VDJ_1_cdr3"] == "GCCAGCAGCTGGATGGATAGGGGTGAAGCTTTC"
    assert cell1["IR_VDJ_1_fwr4_aa"] == "GQGTRLTVV"
    assert cell1["IR_VDJ_1_fwr4"] == "GGACAAGGCACCAGACTCACAGTTGTAG"

    assert cell1["IR_VJ_1_fwr1_aa"] == "AQTVTQSQPEMSVQEAETVTLSCTYD"
    assert (
        cell1["IR_VJ_1_fwr1"]
        == "GCTCAGACAGTCACTCAGTCTCAACCAGAGATGTCTGTGCAGGAGGCAGAGACCGTGACCCTGAGCTGCACATATGAC"
    )
    assert cell1["IR_VJ_1_cdr1_aa"] == "TSESDYY"
    assert cell1["IR_VJ_1_cdr1"] == "ACCAGTGAGAGTGATTATTAT"
    assert cell1["IR_VJ_1_fwr2_aa"] == "LFWYKQPPSRQMILVIR"
    assert (
        cell1["IR_VJ_1_fwr2"] == "TTATTCTGGTACAAGCAGCCTCCCAGCAGGCAGATGATTCTCGTTATTCGC"
    )
    assert cell1["IR_VJ_1_cdr2_aa"] == "QEAYKQQN"
    assert cell1["IR_VJ_1_cdr2"] == "CAAGAAGCTTATAAGCAACAGAAT"
    assert cell1["IR_VJ_1_fwr3_aa"] == "ATENRFSVNFQKAAKSFSLKISDSQLGDAAMYF"
    assert (
        cell1["IR_VJ_1_fwr3"]
        == "GCAACAGAGAATCGTTTCTCTGTGAACTTCCAGAAAGCAGCCAAATCCTTCAGTCTCAAGATCTCAGACTCACAGCTGGGGGATGCCGCGATGTATTTC"
    )
    assert cell1["IR_VJ_1_cdr3_aa"] == "ALYKVTGNQFY"
    assert cell1["IR_VJ_1_cdr3"] == "GCTCTTTATAAGGTCACCGGTAACCAGTTCTAT"
    assert cell1["IR_VJ_1_fwr4_aa"] == "GTGTSLTVIP"
    assert cell1["IR_VJ_1_fwr4"] == "GGGACAGGGACAAGTTTGACGGTCATTCCAA"


@pytest.mark.conda
def test_read_10x_json_cr6():
    """Test additional cols from CR6 outputs: fwr{1,2,3,4}{,_nt} and cdr{1,2}{,_nt}"""

    anndata = read_10x_vdj(
        TESTDATA
        / "10x/10k_BMMNC_5pv2_nextgem_Multiplex_vdj_t_all_contig_annotations_small.json",
        include_fields=None,
    )
    obs = anndata.obs
    assert obs.shape[0] == 2
    cell1 = obs.iloc[0, :]

    assert cell1.name == "AAACCTGCACAGGTTT-1"
    assert cell1["IR_VDJ_1_fwr1_aa"] == "KAGVTQTPRYLIKTRGQQVTLSCSPI"
    assert (
        cell1["IR_VDJ_1_fwr1"]
        == "AAGGCTGGAGTCACTCAAACTCCAAGATATCTGATCAAAACGAGAGGACAGCAAGTGACACTGAGCTGCTCCCCTATC"
    )
    assert cell1["IR_VDJ_1_cdr1_aa"] == "SGHRS"
    assert cell1["IR_VDJ_1_cdr1"] == "TCTGGGCATAGGAGT"
    assert cell1["IR_VDJ_1_fwr2_aa"] == "VSWYQQTPGQGLQFLFE"
    assert (
        cell1["IR_VDJ_1_fwr2"] == "GTATCCTGGTACCAACAGACCCCAGGACAGGGCCTTCAGTTCCTCTTTGAA"
    )
    assert cell1["IR_VDJ_1_cdr2_aa"] == "YFSETQ"
    assert cell1["IR_VDJ_1_cdr2"] == "TACTTCAGTGAGACACAG"
    assert cell1["IR_VDJ_1_fwr3_aa"] == "RNKGNFPGRFSGRQFSNSRSEMNVSTLELGDSALYL"
    assert (
        cell1["IR_VDJ_1_fwr3"]
        == "AGAAACAAAGGAAACTTCCCTGGTCGATTCTCAGGGCGCCAGTTCTCTAACTCTCGCTCTGAGATGAATGTGAGCACCTTGGAGCTGGGGGACTCGGCCCTTTATCTT"
    )
    assert cell1["IR_VDJ_1_cdr3_aa"] == "ASSWMDRGEAF"
    assert cell1["IR_VDJ_1_cdr3"] == "GCCAGCAGCTGGATGGATAGGGGTGAAGCTTTC"
    assert cell1["IR_VDJ_1_fwr4_aa"] == "GQGTRLTVV"
    assert cell1["IR_VDJ_1_fwr4"] == "GGACAAGGCACCAGACTCACAGTTGTAG"

    assert cell1["IR_VJ_1_fwr1_aa"] == "AQTVTQSQPEMSVQEAETVTLSCTYD"
    assert (
        cell1["IR_VJ_1_fwr1"]
        == "GCTCAGACAGTCACTCAGTCTCAACCAGAGATGTCTGTGCAGGAGGCAGAGACCGTGACCCTGAGCTGCACATATGAC"
    )
    assert cell1["IR_VJ_1_cdr1_aa"] == "TSESDYY"
    assert cell1["IR_VJ_1_cdr1"] == "ACCAGTGAGAGTGATTATTAT"
    assert cell1["IR_VJ_1_fwr2_aa"] == "LFWYKQPPSRQMILVIR"
    assert (
        cell1["IR_VJ_1_fwr2"] == "TTATTCTGGTACAAGCAGCCTCCCAGCAGGCAGATGATTCTCGTTATTCGC"
    )
    assert cell1["IR_VJ_1_cdr2_aa"] == "QEAYKQQN"
    assert cell1["IR_VJ_1_cdr2"] == "CAAGAAGCTTATAAGCAACAGAAT"
    assert cell1["IR_VJ_1_fwr3_aa"] == "ATENRFSVNFQKAAKSFSLKISDSQLGDAAMYF"
    assert (
        cell1["IR_VJ_1_fwr3"]
        == "GCAACAGAGAATCGTTTCTCTGTGAACTTCCAGAAAGCAGCCAAATCCTTCAGTCTCAAGATCTCAGACTCACAGCTGGGGGATGCCGCGATGTATTTC"
    )
    assert cell1["IR_VJ_1_cdr3_aa"] == "ALYKVTGNQFY"
    assert cell1["IR_VJ_1_cdr3"] == "GCTCTTTATAAGGTCACCGGTAACCAGTTCTAT"
    assert cell1["IR_VJ_1_fwr4_aa"] == "GTGTSLTVIP"
    assert cell1["IR_VJ_1_fwr4"] == "GGGACAGGGACAAGTTTGACGGTCATTCCAA"


@pytest.mark.conda
def test_read_10x():
    anndata = read_10x_vdj(
        TESTDATA / "10x/all_contig_annotations.json", include_fields=None
    )
    obs = anndata.obs
    # this has `is_cell=false` and should be filtered out
    assert "AAACCTGAGACCTTTG-1" not in anndata.obs_names
    assert obs.shape[0] == 3
    cell1 = obs.iloc[0, :]
    cell2 = obs.iloc[1, :]
    cell3 = obs.iloc[2, :]

    assert cell1.name == "AAACCTGAGACCTTTG-2"
    assert cell1["IR_VDJ_1_junction_aa"] == "CASSPPSQGLSTGELFF"
    assert (
        cell1["IR_VDJ_1_junction"]
        == "TGTGCCAGCTCACCACCGAGCCAGGGCCTTTCTACCGGGGAGCTGTTTTTT"
    )
    assert cell1["IR_VDJ_1_np1_length"] == 4
    assert cell1["IR_VDJ_1_np2_length"] == 7
    assert cell1["IR_VDJ_1_duplicate_count"] == 1
    assert cell1["IR_VDJ_1_consensus_count"] == 494
    assert cell1["IR_VDJ_1_v_call"] == "TRBV18"
    assert cell1["IR_VDJ_1_d_call"] == "TRBD1"
    assert cell1["IR_VDJ_1_j_call"] == "TRBJ2-2"
    assert cell1["IR_VDJ_1_c_call"] == "TRBC2"
    assert _is_false(cell1["multi_chain"])
    assert np.all(
        _is_na(
            cell1[["IR_VJ_1_junction_aa", "IR_VDJ_2_junction_aa", "IR_VJ_1_np1_length"]]
        )
    )

    assert cell2.name == "AAACCTGAGTACGCCC-1"
    assert cell2["IR_VJ_1_junction_aa"] == "CAMRVGGSQGNLIF"
    assert cell2["IR_VJ_2_junction_aa"] == "CATDAKDSNYQLIW"
    assert cell2["IR_VJ_1_duplicate_count"] == 9
    assert cell2["IR_VJ_2_duplicate_count"] == 4
    assert np.all(_is_na(cell2[["IR_VDJ_1_junction_aa", "IR_VDJ_2_junction_aa"]]))
    assert cell2["IR_VJ_1_np1_length"] == 4
    assert _is_na(cell2["IR_VJ_1_np2_length"])
    assert cell2["IR_VJ_2_np1_length"] == 4
    assert _is_na(cell2["IR_VJ_2_np2_length"])

    assert cell3.name == "CAGGTGCTCGTGGTCG-1"
    assert cell3["IR_VJ_1_locus"] == "IGK"
    assert _is_na(cell3["IR_VJ_2_locus"])  # non-productive
    assert cell3["IR_VDJ_1_locus"] == "IGH"
    assert _is_na(cell3["IR_VDJ_2_locus"])  # non-productive


@pytest.mark.conda
def test_read_tracer():
    with pytest.raises(IOError):
        anndata = read_tracer(TESTDATA / "10x")

    anndata = read_tracer(TESTDATA / "tracer")
    assert "cell1" in anndata.obs_names and "cell2" in anndata.obs_names
    assert anndata.obs.shape[0] == 3

    cell1 = anndata.obs.loc["cell1", :]
    cell2 = anndata.obs.loc["cell2", :]

    assert cell1.name == "cell1"
    assert cell1["IR_VJ_1_junction_aa"] == "AESTGTSGTYKYI"
    assert cell1["IR_VDJ_1_junction_aa"] == "ASSYSVSRSGELF"

    assert cell2.name == "cell2"
    assert cell2["IR_VJ_1_junction_aa"] == "ALSEAEGGSEKLV"
    assert cell2["IR_VDJ_1_junction_aa"] == "ASSYNRGPGGTQY"
    assert cell2["IR_VDJ_1_j_call"] == "TRBJ2-5"


@pytest.mark.conda
def test_read_airr_issue280():
    """Test that reading the example shown in issue #280 works."""
    anndata = read_airr(TESTDATA / "airr" / "tra_issue_280.tsv")
    assert anndata.obs["IR_VDJ_1_junction_aa"][0] == "CASSLGGESQNTLYF"
    assert anndata.obs["IR_VJ_1_junction_aa"][0] == "CAARGNRIFF"


@pytest.mark.conda
def test_read_airr():
    # Test that reading the files one-by-one or at once yields the same results
    anndata_tra = read_airr(TESTDATA / "airr/rearrangement_tra.tsv")
    anndata_trb = read_airr(TESTDATA / "airr/rearrangement_trb.tsv")
    anndata_ig = read_airr(TESTDATA / "airr/rearrangement_ig.tsv")
    anndata = read_airr(
        [
            TESTDATA / "airr/rearrangement_tra.tsv",
            TESTDATA / "airr/rearrangement_trb.tsv",
            TESTDATA / "airr/rearrangement_ig.tsv",
        ]
    )
    tra_cols = [
        "IR_VJ_1_junction_aa",
        "IR_VJ_1_junction",
        "IR_VJ_1_v_call",
        "IR_VJ_1_d_call",
        "IR_VJ_1_j_call",
        "IR_VJ_1_c_call",
        "IR_VJ_1_consensus_count",
    ]
    trb_cols = [x.replace("IR_VJ", "IR_VDJ") for x in tra_cols]
    ig_cols = tra_cols + trb_cols
    pdt.assert_frame_equal(
        anndata.obs.loc[anndata.obs["IR_VJ_1_locus"] == "TRA", tra_cols],
        anndata_tra.obs.loc[:, tra_cols],
        check_categorical=False,  # categories differ, obviously
        check_dtype=False,
    )
    pdt.assert_frame_equal(
        anndata.obs.loc[anndata.obs["IR_VDJ_1_locus"] == "TRB", trb_cols],
        anndata_trb.obs.loc[:, trb_cols],
        check_categorical=False,  # categories differ, obviously
        check_dtype=False,
    )
    pdt.assert_frame_equal(
        anndata.obs.loc[anndata.obs["IR_VDJ_1_locus"] == "IGH", ig_cols],
        anndata_ig.obs.loc[:, ig_cols],
        check_categorical=False,  # categories differ, obviously
        check_dtype=False,
    )

    # test some fundamental values
    assert anndata.obs.shape[0] == 5

    cell1 = anndata.obs.loc["cell1", :]
    cell2 = anndata.obs.loc["cell2", :]
    cell3 = anndata.obs.loc["AAACCTGCAGCGTAAG-1", :]

    assert cell1.name == "cell1"
    assert cell1["IR_VJ_1_junction_aa"] == "CTRPKWESPMVDAFDIW"
    assert cell1["IR_VDJ_2_junction_aa"] == "CQQYDNLQITF"
    assert cell1["IR_VDJ_1_junction_aa"] == "CQQYYHTPYSF"
    assert cell1["IR_VJ_1_locus"] == "TRA"
    assert cell1["IR_VDJ_1_locus"] == "TRB"

    assert cell2.name == "cell2"

    # check that inferring the locus name from genes works
    assert cell3["IR_VJ_1_locus"] == "IGL"
    assert cell3["IR_VDJ_1_locus"] == "IGH"


def test_airr_df():
    # Test that reading AIRR data from a data frame works as expected.
    # Regression test for #348
    df = pd.DataFrame(
        [
            # IGHM+IGHD+IGHL+IGHAnp
            [
                "IGHM+IGHD+IGHL+IGHAnp_contig_1",
                "",
                "T",
                "T",
                "IGHV1",
                "IGHD1-2",
                "IGHJ2-1",
                "TGGGGAGGAGTCAGTCCCAACCAGGACACGGCCTGGACATGAGGGTCCCTGCTCAGCTCCTGGGGCTCCTGCTGCTCTGGCTCTCAGGTGCCAGATGTGACATCCAGATGACCCAGTCTCCATCCTCCCTGTCTGCATCTGTGGGAGACAGAGTCACCATCACTTGCCAGGCGACACAAGACATTAACAATTATGTAAATTGGTATCAGCAGAAACCAGGGAAAGCCCCTAAACTCCTGATCTACGATGCATTGAATTTAGAAATAGGGGTCCCATCAAGATTCAGTGGAAGAGGGTCTGGGACAGTCTTTATTCTCACCATCAGCAGCCTGCAGCCTGAAGATGTTGCAACATACTACTGTCAACAATATGACGAACTTCCCGTCACTTTCGGCGGAGGGACCAATGTGGAAATGAGACGAACTGTGGCTGCACCATCTGTCTTCATCTTCCCGCCATCTGATGAGCAGTTGAAATCTGGAACTGCCTCTGTTGTGTGCCTGCTGAATAACTTCTATCCCAGAGAGGCCAAAGTACAGTGGAAGGTGGATAACGC",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGH",
                1,
                1,
                1,
                "IGHM+IGHD+IGHL+IGHAnp",
                "IGHM",
                1,
                1,
            ],
            [
                "IGHM+IGHD+IGHL+IGHAnp_contig_2",
                "",
                "T",
                "T",
                "IGLV1",
                "",
                "IGLJ2-1",
                "AGGAGTCAGACCCTGTCAGGACACAGCATAGACATGAGGGTCCCCGCTCAGCTCCTGGGGCTCCTGCTGCTCTGGCTCCCAGGTGCCAGATGTGCCATCCGGATGACCCAGTCTCCATCCTCATTCTCTGCATCTACAGGAGACAGAGTCACCATCACTTGTCGGGCGAGTCAGGGTATTAGCAGTTATTTAGCCTGGTATCAGCAAAAACCAGGGAAAGCCCCTAAGCTCCTGATCTATGCTGCATCCACTTTGCAAAGTGGGGTCCCATCAAGGTTCAGCGGCAGTGGATCTGGGACAGATTTCACTCTCACCATCAGCTGCCTGCAGTCTGAAGATTTTGCAACTTATTACTGTCAACAGTATTATAGTTACCCTCGGACGTTCGGCCAAGGGACCAAGGTGGAAATCAAACGAACTGTGGCTGCACCATCTGTCTTCATCTTCCCGCCATCTGATGAGCAGTTGAAATCTGGAACTGCCTCTGTTGTGTGCCTGCTGAATAACTTCTATCCCAGAGAGGCCAAAGTACAGTGGAAGGTGGATAACGC",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGL",
                1,
                1,
                1,
                "IGHM+IGHD+IGHL+IGHAnp",
                "IGLC",
                1,
                1,
            ],
            [
                "IGHM+IGHD+IGHL+IGHAnp_contig_3",
                "",
                "T",
                "F",
                "IGHV1",
                "IGHD1-2",
                "IGHJ2-1",
                "",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGH",
                1,
                1,
                1,
                "IGHM+IGHD+IGHL+IGHAnp",
                "IGHD",
                1,
                10,
            ],
            [
                "IGHM+IGHD+IGHL+IGHAnp_contig_4",
                "",
                "T",
                "F",
                "IGHV1",
                "IGHD1",
                "IGHJ2-1",
                "TGGGGAGGAGTCAGTCCCAACCAGGACACGGCCTGGACATGAGGGTCCCTGCTCAGCTCCTGGGGCTCCTGCTGCTCTGGCTCTCAGGTGCCAGATGTGACATCCAGATGACCCAGTCTCCATCCTCCCTGTCTGCATCTGTGGGAGACAGAGTCACCATCACTTGCCAGGCGACACAAGACATTAACAATTATGTAAATTGGTATCAGCAGAAACCAGGGAAAGCCCCTAAACTCCTGATCTACGATGCATTGAATTTAGAAATAGGGGTCCCATCAAGATTCAGTGGAAGAGGGTCTGGGACAGTCTTTATTCTCACCATCAGCAGCCTGCAGCCTGAAGATGTTGCAACATACTACTGTCAACAATATGACGAACTTCCCGTCACTTTCGGCGGAGGGACCAATGTGGAAATGAGACGAACTGTGGCTGCACCATCTGTCTTCATCTTCCCGCCATCTGATGAGCAGTTGAAATCTGGAACTGCCTCTGTTGTGTGCCTGCTGAATAACTTCTATCCCAGAGAGGCCAAAGTACAGTGGAAGGTGGATAACGC",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGH",
                1,
                1,
                1,
                "IGHM+IGHD+IGHL+IGHAnp",
                "IGHA",
                1,
                1,
            ],
            # IGHM+IGHAnp
            [
                "IGHM+IGHAnp_contig_1",
                "",
                "T",
                "T",
                "IGHV1",
                "IGHD1-2",
                "IGHJ2-1",
                "TGGGGAGGAGTCAGTCCCAACCAGGACACGGCCTGGACATGAGGGTCCCTGCTCAGCTCCTGGGGCTCCTGCTGCTCTGGCTCTCAGGTGCCAGATGTGACATCCAGATGACCCAGTCTCCATCCTCCCTGTCTGCATCTGTGGGAGACAGAGTCACCATCACTTGCCAGGCGACACAAGACATTAACAATTATGTAAATTGGTATCAGCAGAAACCAGGGAAAGCCCCTAAACTCCTGATCTACGATGCATTGAATTTAGAAATAGGGGTCCCATCAAGATTCAGTGGAAGAGGGTCTGGGACAGTCTTTATTCTCACCATCAGCAGCCTGCAGCCTGAAGATGTTGCAACATACTACTGTCAACAATATGACGAACTTCCCGTCACTTTCGGCGGAGGGACCAATGTGGAAATGAGACGAACTGTGGCTGCACCATCTGTCTTCATCTTCCCGCCATCTGATGAGCAGTTGAAATCTGGAACTGCCTCTGTTGTGTGCCTGCTGAATAACTTCTATCCCAGAGAGGCCAAAGTACAGTGGAAGGTGGATAACGC",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGH",
                1,
                1,
                1,
                "IGHM+IGHAnp",
                "IGHM",
                1,
                1,
            ],
            [
                "IGHM+IGHAnp_contig_2",
                "",
                "T",
                "F",
                "IGHV1",
                "IGHD1",
                "IGHJ-1",
                "AGGAGTCAGACCCTGTCAGGACACAGCATAGACATGAGGGTCCCCGCTCAGCTCCTGGGGCTCCTGCTGCTCTGGCTCCCAGGTGCCAGATGTGCCATCCGGATGACCCAGTCTCCATCCTCATTCTCTGCATCTACAGGAGACAGAGTCACCATCACTTGTCGGGCGAGTCAGGGTATTAGCAGTTATTTAGCCTGGTATCAGCAAAAACCAGGGAAAGCCCCTAAGCTCCTGATCTATGCTGCATCCACTTTGCAAAGTGGGGTCCCATCAAGGTTCAGCGGCAGTGGATCTGGGACAGATTTCACTCTCACCATCAGCTGCCTGCAGTCTGAAGATTTTGCAACTTATTACTGTCAACAGTATTATAGTTACCCTCGGACGTTCGGCCAAGGGACCAAGGTGGAAATCAAACGAACTGTGGCTGCACCATCTGTCTTCATCTTCCCGCCATCTGATGAGCAGTTGAAATCTGGAACTGCCTCTGTTGTGTGCCTGCTGAATAACTTCTATCCCAGAGAGGCCAAAGTACAGTGGAAGGTGGATAACGC",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGH",
                1,
                1,
                1,
                "IGHM+IGHAnp",
                "IGHA1",
                1,
                1,
            ],
            # IGHM+IGHAnp2 np has highest umi
            [
                "IGHM+IGHAnp2_contig_1",
                "",
                "T",
                "T",
                "IGHV1",
                "IGHD1-2",
                "IGHJ2-1",
                "TGGGGAGGAGTCAGTCCCAACCAGGACACGGCCTGGACATGAGGGTCCCTGCTCAGCTCCTGGGGCTCCTGCTGCTCTGGCTCTCAGGTGCCAGATGTGACATCCAGATGACCCAGTCTCCATCCTCCCTGTCTGCATCTGTGGGAGACAGAGTCACCATCACTTGCCAGGCGACACAAGACATTAACAATTATGTAAATTGGTATCAGCAGAAACCAGGGAAAGCCCCTAAACTCCTGATCTACGATGCATTGAATTTAGAAATAGGGGTCCCATCAAGATTCAGTGGAAGAGGGTCTGGGACAGTCTTTATTCTCACCATCAGCAGCCTGCAGCCTGAAGATGTTGCAACATACTACTGTCAACAATATGACGAACTTCCCGTCACTTTCGGCGGAGGGACCAATGTGGAAATGAGACGAACTGTGGCTGCACCATCTGTCTTCATCTTCCCGCCATCTGATGAGCAGTTGAAATCTGGAACTGCCTCTGTTGTGTGCCTGCTGAATAACTTCTATCCCAGAGAGGCCAAAGTACAGTGGAAGGTGGATAACGC",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGH",
                1,
                1,
                1,
                "IGHM+IGHAnp2",
                "IGHM",
                1,
                1,
            ],
            [
                "IGHM+IGHAnp2_contig_2",
                "",
                "T",
                "F",
                "IGHV1",
                "IGHD1",
                "IGHJ-1",
                "AGGAGTCAGACCCTGTCAGGACACAGCATAGACATGAGGGTCCCCGCTCAGCTCCTGGGGCTCCTGCTGCTCTGGCTCCCAGGTGCCAGATGTGCCATCCGGATGACCCAGTCTCCATCCTCATTCTCTGCATCTACAGGAGACAGAGTCACCATCACTTGTCGGGCGAGTCAGGGTATTAGCAGTTATTTAGCCTGGTATCAGCAAAAACCAGGGAAAGCCCCTAAGCTCCTGATCTATGCTGCATCCACTTTGCAAAGTGGGGTCCCATCAAGGTTCAGCGGCAGTGGATCTGGGACAGATTTCACTCTCACCATCAGCTGCCTGCAGTCTGAAGATTTTGCAACTTATTACTGTCAACAGTATTATAGTTACCCTCGGACGTTCGGCCAAGGGACCAAGGTGGAAATCAAACGAACTGTGGCTGCACCATCTGTCTTCATCTTCCCGCCATCTGATGAGCAGTTGAAATCTGGAACTGCCTCTGTTGTGTGCCTGCTGAATAACTTCTATCCCAGAGAGGCCAAAGTACAGTGGAAGGTGGATAACGC",
                "",
                "CATTGCTCTCACTTCCTAGACCCATACTCAGCTCTCTGTGTT",
                "HCSHFLDPYSALCV",
                "",
                "",
                "",
                "F",
                "T",
                "IGH",
                1,
                1,
                1,
                "IGHM+IGHAnp2",
                "IGHA1",
                1,
                100,
            ],
        ],
        columns=[
            "sequence_id",
            "sequence",
            "rev_comp",
            "productive",
            "v_call",
            "d_call",
            "j_call",
            "sequence_alignment",
            "germline_alignment",
            "junction",
            "junction_aa",
            "v_cigar",
            "d_cigar",
            "j_cigar",
            "stop_codon",
            "vj_in_frame",
            "locus",
            "junction_length",
            "np1_length",
            "np2_length",
            "cell_id",
            "c_call",
            "consensus_count",
            "duplicate_count",
        ],
    )

    adata = read_airr(df)
    ir.tl.chain_qc(adata)
    assert adata.obs["receptor_subtype"].tolist() == ["IGH+IGL", "IGH", "IGH"]
    assert adata.obs["chain_pairing"].tolist() == [
        "single pair",
        "orphan VDJ",
        "orphan VDJ",
    ]
    assert adata.obs["IR_VJ_1_locus"].tolist() == ["IGL", np.nan, np.nan]
    assert adata.obs["IR_VDJ_1_locus"].tolist() == ["IGH"] * 3


@pytest.mark.conda
def test_read_bracer():
    anndata = read_bracer(TESTDATA / "bracer/changeodb.tab")
    assert "SRR10788834" in anndata.obs.index
    assert anndata.obs.shape[0] == 6

    cell1 = anndata.obs.loc["SRR10779208", :]
    cell2 = anndata.obs.loc["SRR10788834", :]

    assert cell1.name == "SRR10779208"
    assert cell1["IR_VJ_1_locus"] == "IGK"
    assert cell1["IR_VDJ_1_locus"] == "IGH"
    assert cell1["IR_VDJ_1_j_call"] == "IGHJ4"
    assert cell1["IR_VDJ_1_junction"] == "TGTGCGACGATGACGGGGGGTGACCTTGACTACTGG"
    assert cell1["IR_VDJ_1_junction_aa"] == "CATMTGGDLDYW"
    assert cell1["IR_VJ_1_np1_length"] == 1
    assert _is_na(cell1["IR_VJ_1_np2_length"])

    assert cell2.name == "SRR10788834"
    assert cell2["IR_VDJ_1_junction_aa"] == "CARDHIVVLEPTPKRYGMDVW"
    assert (
        cell2["IR_VDJ_1_junction"]
        == "TGTGCGAGAGATCATATTGTAGTCTTGGAACCTACCCCTAAGAGATACGGTATGGACGTCTGG"
    )
    assert cell2["IR_VDJ_1_np1_length"] == 2
    assert cell2["IR_VDJ_1_np2_length"] == 22


@pytest.mark.conda
def test_read_bd_per_cell_chain():
    adata = read_bd_rhapsody(TESTDATA / "bd/test_per_cell_chain.csv")

    assert adata.obs.shape[0] == 7

    cell1 = adata.obs.loc["1", :]
    cell25 = adata.obs.loc["25", :]
    cell39 = adata.obs.loc["39", :]
    cell85 = adata.obs.loc["85", :]

    assert cell1["IR_VJ_1_locus"] == "TRA"
    assert cell1["IR_VJ_1_duplicate_count"] == 1
    assert cell1["IR_VJ_1_consensus_count"] == 72
    assert cell1["IR_VJ_1_junction"] == "GCTGCCCCAGAATTTTGTC"
    assert cell1["IR_VJ_1_junction_aa"] == "AAGQNFV"
    assert cell1["IR_VJ_1_v_gene"] == "TRAV38*01"
    assert _is_na(cell1["IR_VJ_1_d_gene"])
    assert cell1["IR_VJ_1_j_gene"] == "TRAJ2*01"
    assert cell1["IR_VJ_1_c_gene"] == "TRAC"

    # cell25 has no productive chains
    assert not _is_na(cell25["extra_chains"])
    assert _is_na(cell25["IR_VJ_1_locus"])

    assert cell39["IR_VJ_1_locus"] == "TRG"
    assert cell39["IR_VDJ_1_locus"] == "TRD"

    assert cell85["IR_VJ_1_locus"] == "TRA"
    assert cell85["IR_VJ_1_consensus_count"] == 418
    assert cell85["IR_VJ_2_locus"] == "TRA"
    assert cell85["IR_VJ_1_consensus_count"] == 1
    assert cell85["extra_chains"].contains("TRA")


@pytest.mark.conda
def test_read_bd_contigs():
    adata = read_bd_rhapsody(TESTDATA / "bd/test_unfiltered_contigs.csv")

    assert adata.obs.shape[0] == 5

    cell10681 = adata.obs.loc["10681"]

    assert cell10681["IR_VJ_1_locus"] == "IGK"
    assert cell10681["IR_VJ_1_duplicate_count"] == 2
