"""Tests for snap7.optimizer — the 3-stage read optimization pipeline."""

from snap7.type import Area
from snap7.optimizer import (
    ReadItem,
    ReadBlock,
    sort_items,
    merge_items,
    packetize,
    optimize_reads,
    extract_results,
    _max_byte_request,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(
    area: Area = Area.DB,
    db: int = 1,
    offset: int = 0,
    bit: int = 0,
    length: int = 4,
    index: int = 0,
) -> ReadItem:
    return ReadItem(area=area, db_number=db, byte_offset=offset, bit_offset=bit, byte_length=length, index=index)


# ---------------------------------------------------------------------------
# TestSortItems
# ---------------------------------------------------------------------------


class TestSortItems:
    def test_sort_by_area(self) -> None:
        items = [_item(area=Area.DB, index=0), _item(area=Area.MK, index=1), _item(area=Area.PE, index=2)]
        result = sort_items(items)
        assert [r.area for r in result] == [Area.PE, Area.MK, Area.DB]

    def test_sort_by_db_number(self) -> None:
        items = [_item(db=3, index=0), _item(db=1, index=1), _item(db=2, index=2)]
        result = sort_items(items)
        assert [r.db_number for r in result] == [1, 2, 3]

    def test_sort_by_offset(self) -> None:
        items = [_item(offset=10, index=0), _item(offset=0, index=1), _item(offset=5, index=2)]
        result = sort_items(items)
        assert [r.byte_offset for r in result] == [0, 5, 10]

    def test_sort_by_bit_offset(self) -> None:
        items = [_item(offset=0, bit=3, index=0), _item(offset=0, bit=0, index=1)]
        result = sort_items(items)
        assert [r.bit_offset for r in result] == [0, 3]

    def test_sort_by_length_descending(self) -> None:
        items = [_item(offset=0, length=2, index=0), _item(offset=0, length=4, index=1)]
        result = sort_items(items)
        assert [r.byte_length for r in result] == [4, 2]

    def test_empty(self) -> None:
        assert sort_items([]) == []

    def test_single(self) -> None:
        items = [_item(index=0)]
        assert sort_items(items) == items


# ---------------------------------------------------------------------------
# TestMergeItems
# ---------------------------------------------------------------------------


class TestMergeItems:
    def test_contiguous_merge(self) -> None:
        """Two adjacent items in the same area/DB should merge."""
        items = sort_items(
            [
                _item(offset=0, length=4, index=0),
                _item(offset=4, length=4, index=1),
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 1
        assert blocks[0].start_offset == 0
        assert blocks[0].byte_length == 8

    def test_gap_within_threshold(self) -> None:
        """Items with a gap ≤ max_gap should merge."""
        items = sort_items(
            [
                _item(offset=0, length=4, index=0),
                _item(offset=7, length=4, index=1),  # gap = 3 bytes
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 1
        assert blocks[0].byte_length == 11  # 0..10

    def test_gap_exceeding_threshold(self) -> None:
        """Items with a gap > max_gap should NOT merge."""
        items = sort_items(
            [
                _item(offset=0, length=4, index=0),
                _item(offset=20, length=4, index=1),  # gap = 16
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 2

    def test_cross_area_no_merge(self) -> None:
        items = sort_items(
            [
                _item(area=Area.MK, db=0, offset=0, length=4, index=0),
                _item(area=Area.DB, db=1, offset=0, length=4, index=1),
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 2

    def test_cross_db_no_merge(self) -> None:
        items = sort_items(
            [
                _item(db=1, offset=0, length=4, index=0),
                _item(db=2, offset=0, length=4, index=1),
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 2

    def test_non_optimizable_no_merge(self) -> None:
        """CT/TM items should never merge, even if contiguous."""
        items = sort_items(
            [
                _item(area=Area.CT, db=0, offset=0, length=2, index=0),
                _item(area=Area.CT, db=0, offset=2, length=2, index=1),
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 2

    def test_max_byte_request_overflow(self) -> None:
        """Merged length exceeding max_byte_request should start a new block."""
        items = sort_items(
            [
                _item(offset=0, length=100, index=0),
                _item(offset=100, length=100, index=1),
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=150)
        assert len(blocks) == 2

    def test_empty(self) -> None:
        assert merge_items([], max_gap=5, max_byte_request=200) == []

    def test_worked_example_6vars_3blocks(self) -> None:
        """Worked example: 6 variables → 3 blocks with max_gap=5."""
        items = sort_items(
            [
                # Block 1: DB1, offsets 0-8 (contiguous)
                _item(db=1, offset=0, length=4, index=0),
                _item(db=1, offset=4, length=4, index=1),
                # Block 2: DB1, offset 20 (gap > 5 from block 1 end at 8)
                _item(db=1, offset=20, length=4, index=2),
                _item(db=1, offset=24, length=2, index=3),  # contiguous with above
                # Block 3: DB2
                _item(db=2, offset=0, length=4, index=4),
                _item(db=2, offset=2, length=4, index=5),  # overlapping
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 3
        assert blocks[0].byte_length == 8  # DB1 0..7
        assert blocks[1].byte_length == 6  # DB1 20..25
        assert blocks[2].byte_length == 6  # DB2 0..5

    def test_overlapping_items(self) -> None:
        """Overlapping items should merge correctly without double-counting."""
        items = sort_items(
            [
                _item(offset=0, length=8, index=0),
                _item(offset=4, length=4, index=1),  # fully within first
            ]
        )
        blocks = merge_items(items, max_gap=5, max_byte_request=200)
        assert len(blocks) == 1
        assert blocks[0].byte_length == 8  # Not 12


# ---------------------------------------------------------------------------
# TestPacketize
# ---------------------------------------------------------------------------


class TestPacketize:
    def test_all_fit_one_packet(self) -> None:
        blocks = [
            ReadBlock(area=Area.DB, db_number=1, start_offset=0, byte_length=4, items=[]),
            ReadBlock(area=Area.DB, db_number=1, start_offset=10, byte_length=4, items=[]),
        ]
        packets = packetize(blocks, pdu_size=480)
        assert len(packets) == 1
        assert len(packets[0].blocks) == 2

    def test_reply_overflow_splits(self) -> None:
        """Large data blocks should cause packet splitting due to reply budget."""
        # PDU 240: reply budget starts at 14.
        # A 200-byte block costs 200 + 0 (even) + 4 = 204, total 218 < 240 OK
        # Adding another 200-byte block would be 218 + 204 = 422 > 240
        blocks = [
            ReadBlock(area=Area.DB, db_number=1, start_offset=0, byte_length=200, items=[]),
            ReadBlock(area=Area.DB, db_number=1, start_offset=300, byte_length=200, items=[]),
        ]
        packets = packetize(blocks, pdu_size=240)
        assert len(packets) == 2

    def test_request_overflow_splits(self) -> None:
        """Many small blocks should cause splitting due to request budget (12 bytes per address spec)."""
        # PDU 240: request budget starts at 12, each block adds 12.
        # Max blocks per packet: (240 - 12) / 12 = 19
        blocks = [ReadBlock(area=Area.DB, db_number=1, start_offset=i * 10, byte_length=2, items=[]) for i in range(25)]
        packets = packetize(blocks, pdu_size=240)
        assert len(packets) >= 2

    def test_fill_byte_accounting(self) -> None:
        """Odd-length blocks require a fill byte in the reply budget."""
        # A 3-byte block costs 3 + 1 (fill) + 4 = 8 in reply budget
        block = ReadBlock(area=Area.DB, db_number=1, start_offset=0, byte_length=3, items=[])
        packets = packetize([block], pdu_size=480)
        assert len(packets) == 1

    def test_block_splitting(self) -> None:
        """Blocks exceeding max_byte_request should be split."""
        # PDU 240 → max_byte_request = 4 * ((240 - 18) // 4) = 4 * 55 = 220
        big_block = ReadBlock(
            area=Area.DB,
            db_number=1,
            start_offset=0,
            byte_length=500,
            items=[_item(offset=0, length=500, index=0)],
        )
        packets = packetize([big_block], pdu_size=240)
        total_bytes = sum(b.byte_length for p in packets for b in p.blocks)
        assert total_bytes == 500

    def test_pdu_240_budget(self) -> None:
        assert _max_byte_request(240) == 4 * ((240 - 18) // 4)

    def test_pdu_960_budget(self) -> None:
        assert _max_byte_request(960) == 4 * ((960 - 18) // 4)

    def test_empty(self) -> None:
        assert packetize([], pdu_size=480) == []


# ---------------------------------------------------------------------------
# TestOptimizeReads
# ---------------------------------------------------------------------------


class TestOptimizeReads:
    def test_full_pipeline(self) -> None:
        """End-to-end pipeline produces valid packets."""
        items = [
            _item(db=1, offset=0, length=4, index=0),
            _item(db=1, offset=4, length=4, index=1),
            _item(db=1, offset=100, length=4, index=2),
            _item(db=2, offset=0, length=8, index=3),
        ]
        packets = optimize_reads(items, pdu_size=480, max_gap=5)
        assert len(packets) >= 1

        # All items should be accounted for
        all_items = [item for p in packets for b in p.blocks for item in b.items]
        assert sorted(item.index for item in all_items) == [0, 1, 2, 3]

    def test_empty(self) -> None:
        assert optimize_reads([], pdu_size=480) == []

    def test_result_extraction(self) -> None:
        """extract_results correctly maps block buffers back to original items."""
        items = [
            _item(db=1, offset=0, length=2, index=0),
            _item(db=1, offset=2, length=2, index=1),
            _item(db=1, offset=10, length=4, index=2),
        ]
        packets = optimize_reads(items, pdu_size=480, max_gap=5)

        # Simulate filling block buffers
        all_blocks: list[ReadBlock] = []
        for packet in packets:
            for block in packet.blocks:
                # Fill with recognizable data
                block.buffer = bytearray(range(block.start_offset, block.start_offset + block.byte_length))
                all_blocks.append(block)

        results = extract_results(items, all_blocks)
        assert len(results) == 3
        # Item 0: offset 0, length 2 → bytes [0, 1]
        assert results[0] == bytearray([0, 1])
        # Item 1: offset 2, length 2 → bytes [2, 3]
        assert results[1] == bytearray([2, 3])
        # Item 2: offset 10, length 4 → bytes [10, 11, 12, 13]
        assert results[2] == bytearray([10, 11, 12, 13])

    def test_single_item(self) -> None:
        items = [_item(db=1, offset=5, length=10, index=0)]
        packets = optimize_reads(items, pdu_size=480)
        assert len(packets) == 1
        assert packets[0].blocks[0].byte_length == 10
